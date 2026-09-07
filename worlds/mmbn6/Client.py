import asyncio
import logging
from typing import TYPE_CHECKING

from NetUtils import ClientStatus
from .._bizhawk import guarded_write, guarded_read, RequestFailedError, read, write, display_message
from .._bizhawk.client import BizHawkClient
from .Locations import all_locations, LocationData, LocationType, scoutable_locations
from .Items import all_items, ItemType, ItemData, programs_to_item_id, chips_amount_index
from .BN6RomUtils import int32_to_byte_list_le, int16_to_byte_list_le

if TYPE_CHECKING:
    from .._bizhawk.context import BizHawkClientContext

logger = logging.getLogger("Client")

ROM_ADDRS = {
    "game_identifier": (0xA0, 10, "ROM"),
    "player_name": (0x7FFFC0, 63, "ROM")
}

RAM_ADDRS = {
    # 0x00: World Initialization
    # 0x04: World
    # 0x08: Battle Init
    # 0x0C: Battle
    # 0x10: Map Change
    # 0x14: Character Change
    # 0x18: Menu
    # 0x1C: BBS
    # 0x20: Shop
    # 0x24: Chip Trader
    # 0x30: Request BBS
    # 0x34: Mailbox
    # 0x38: ChargeMan Minigame
    "game_state": (0x1B80, 1, "EWRAM"),
    #
    "main_area": (0x1B84, 1, "EWRAM"),
    #
    "sub_area": (0x1B85, 1, "EWRAM"),
    # Start location of library array
    "chip_library_start": (0x223C, 1, "EWRAM"),
    "key_item_amount_start": (0x3134, 1, "EWRAM"),
    # Base value is set on new game. Does "Zenny Amount XOR Anticheat Base" to get anticheat value
    "key_item_anticheat_base_start": (0x04E0, 1, "EWRAM"),
    "key_item_anticheat_value_start": (0x4A8C, 1, "EWRAM"),
    "zenny_amount": (0x1BDC, 4, "EWRAM"),
    # Base value is set on new game. Does "Zenny Amount XOR Anticheat Base" to get anticheat value
    "zenny_anticheat_base": (0x0060, 4, "EWRAM"),
    "zenny_anticheat_value": (0x5028, 4, "EWRAM"),
    "bugfrag_amount": (0x1BE0, 4, "EWRAM"),
    # Base value is set on new game. Does "BugFrag Amount XOR Anticheat Base" to get anticheat value
    "bugfrag_anticheat_base": (0x18B8, 4, "EWRAM"),
    "bugfrag_anticheat_value": (0x5030, 4, "EWRAM"),
    # HP values. Base HP is before navicust programs
    "base_hp": (0x480A, 2, "EWRAM"),
    "curr_hp": (0x480C, 2, "EWRAM"),
    "max_hp": (0x480E, 2, "EWRAM"),
    "reg_mem": (0x47D5, 1, "EWRAM"),
    # An arbitrary address that isn't used strictly by the game
    # We'll use it to store the index of the last processed remote item
    # (May actually be used somewhere, but I guess we'll find out)
    "received_index": (0x1B60, 2, "EWRAM"),
    # A set of flags set by early game cutscenes. Since this should be 0x00, we use this to know if RAM can be trusted
    "canary_byte": (0x1D09, 1, "EWRAM"),
    # Contains the victory flag at bit 0x08
    "cybeast_defeated_flag_byte": (0x1E51, 1, "EWRAM"),
    "transformation_flags1": (0x1CA4, 1, "EWRAM"),
    "transformation_flags2": (0x1CA5, 1, "EWRAM"),
    #Virus Battler related flag addresses
    "virus_battler_machine_flags": (0x1CC2, 1, "EWRAM"),
    "virus_battler_enabled_flag": (0x1CC3, 1, "EWRAM"),
    "rare_virus_flags1": (0x1CBA, 1, "EWRAM"),
    "rare_virus_flags4": (0x1CBD, 1, "EWRAM"),
    # Note: This is not the original flag location, these are re-purposed flags
    "link_navi_upgrade_flags": (0x1D14, 1, "EWRAM")
}

SPECIAL_KEY_ITEMS = {
    "BeastOut": 10,
    "HeatCross": 53,
    "SlashCross": 55,
    "ElecCross": 56,
    "EraseCross": 58,
    "ChargeCross": 60,
    "SpoutCross": 53,
    "TenguCross": 55,
    "TomahawkCross": 56,
    "GroundCross": 58,
    "DustCross": 60,
    "LkNvUpgd": 62,
    "BtlrCard": 64
}

gregar_key_items_xor = 0x55
falzar_key_items_xor = 0x6F

class MMBN6Client(BizHawkClient):
    game = "MegaMan Battle Network 6"
    system = "GBA"
    patch_suffix = (".apbn6g", ".apbn6f")
    location_by_id: dict[int, LocationData]
    item_by_id: dict[int, ItemData]
    main_area: int
    sub_area: int
    player_name: str | None
    seed_verify = False
    sent_hints = []
    player_slot = -1
    key_item_xor = 0x00
    game_version = ""

    def __init__(self) -> None:
        super().__init__()
        self.location_by_id = {loc_data.id: loc_data for loc_data in all_locations}
        self.item_by_id = {item_data.code: item_data for item_data in all_items}
        self.main_area = 0x00
        self.sub_area = 0x00

    async def validate_rom(self, ctx: "BizHawkClientContext") -> bool:
        try:
            # Check ROM name/patch version
            rom_name_bytes = (await read(ctx.bizhawk_ctx, [ROM_ADDRS["game_identifier"]]))[0]
            rom_name = bytes([byte for byte in rom_name_bytes if byte != 0]).decode("ascii")

            if rom_name == "MEGAMAN6_G":
                self.key_item_xor = gregar_key_items_xor
                self.game_version = "gregar"
            elif rom_name == "MEGAMAN6_F":
                self.key_item_xor = falzar_key_items_xor
                self.game_version = "falzar"

            if rom_name != "MEGAMAN6_G" and rom_name != "MEGAMAN6_F":
                return False

        except UnicodeDecodeError:
            return False
        except RequestFailedError:
            return False

        ctx.game = self.game
        # We send items, and can have starting inventory set. In some specific circumstances, we need to "send" items
        # found locally (boss checks)
        ctx.items_handling = 0b111
        ctx.want_slot_data = True
        ctx.watcher_timeout = 0.5
        name_bytes = (await read(ctx.bizhawk_ctx, [ROM_ADDRS["player_name"]]))[0]
        name = bytes([byte for byte in name_bytes if byte != 0]).decode("UTF-8")
        self.player_name = name

        return True

    async def set_auth(self, ctx: "BizHawkClientContext") -> None:
        ctx.auth = self.player_name

    async def game_watcher(self, ctx: "BizHawkClientContext") -> None:
        if ctx.server is None or ctx.server.socket.closed or ctx.slot_data is None:
            return

        # Set player_slot
        if self.player_slot == -1:
            for key, slot in ctx.slot_info.items():
                if slot.name == self.player_name:
                    self.player_slot = key
                    break

        try:
            # Handle giving the player items
            read_result = await read(ctx.bizhawk_ctx, [
                RAM_ADDRS["game_state"],  # Current state of game (is the player actually in-game?)
                RAM_ADDRS["main_area"],
                RAM_ADDRS["sub_area"],
                RAM_ADDRS["received_index"],
                RAM_ADDRS["canary_byte"],
                RAM_ADDRS["cybeast_defeated_flag_byte"]
            ])
            if read_result is None:
                return

            game_state = read_result[0][0]
            main_area_id = read_result[1][0]
            sub_area_id = read_result[2][0]
            received_index = (read_result[3][0] << 8) + read_result[3][1]
            canary_byte = read_result[4][0]
            cybeast_defeated_flag_byte = read_result[5][0]

            # Do nothing if canary byte is not 0x00, and the game state is not 0x04 (controlling Lan/MegaMan)
            if canary_byte == 0x00 and game_state == 0x04:
                # Check for goal, Cybeast flag is located at 0x80
                if not ctx.finished_game and (cybeast_defeated_flag_byte | 0x08) == cybeast_defeated_flag_byte:
                    await ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])

                await self.handle_item_receiving(ctx, received_index)
                await self.handle_location_sending(ctx)
                await self.handle_scoutable_locations(ctx)
                await self.handle_special_items(ctx)

                # Player moved to a new room that isn't the pause menu. Pause menu `room_area_id` == 0x0000
                if self.main_area != main_area_id or self.sub_area != sub_area_id:
                    await self.handle_room_change(ctx, main_area_id, sub_area_id)

        except RequestFailedError:
            # The connector didn't respond. Exit handler and return to main loop to reconnect
            pass

    @staticmethod
    async def give_chip(ctx: "BizHawkClientContext", chip) -> bool:
        # First, get the amount of that item we have. Chips surprisingly don't have an anticheat system that I've found.
        # First four bytes are amount, then four 2-byte values indicating pack location. We don't need to bother setting
        # pack location, so just change amount and move on.
        index = chips_amount_index[chip]
        amount = await read(ctx.bizhawk_ctx, [(RAM_ADDRS["chip_library_start"][0] + index, 1, "EWRAM")])

        write_result = False
        total = 0
        while not write_result:
            # Write to the address if it hasn't changed.
            # Anticheat mechanism just XORs the base value with 0x55
            write_result = await guarded_write(ctx.bizhawk_ctx,
                                               [(RAM_ADDRS["chip_library_start"][0] + index, [amount[0][0] + 1], "EWRAM")],
                                               [(RAM_ADDRS["chip_library_start"][0] + index, [amount[0][0]], "EWRAM")])

            await asyncio.sleep(0.05)
            total += 0.05
            if write_result:
                total = 1
            if total > 1:
                return False

        return True

    @staticmethod
    async def give_item(ctx: "BizHawkClientContext", item, xor) -> bool:
        # First, get the amount of that item we have
        amount = await read(ctx.bizhawk_ctx, [(RAM_ADDRS["key_item_amount_start"][0] + item, 1, "EWRAM")])
        # Get the base anticheat value
        anticheat_base = await read(ctx.bizhawk_ctx, [(RAM_ADDRS["key_item_anticheat_base_start"][0] + item, 1, "EWRAM")])

        # If item is SubMemry and amount is already two, return. Never let the player have more than 8 SubMemrys
        if item == 117 and amount[0][0] >= 8:
            return True
        # If item is ExpMemry and amount is already two, return. Never let the player have more than 2 ExpMemrys
        if item == 113 and amount[0][0] >= 2:
            return True

        write_result = False
        total = 0
        while not write_result:
            # Write to the address if it hasn't changed.
            # Anticheat mechanism just XORs the base value with 0x55 or 0x6F, depending on version
            write_result = await guarded_write(ctx.bizhawk_ctx,
                                               [(RAM_ADDRS["key_item_amount_start"][0] + item, [amount[0][0] + 1], "EWRAM"),
                                                (RAM_ADDRS["key_item_anticheat_value_start"][0] + item, [anticheat_base[0][0] ^ xor], "EWRAM")],
                                               [(RAM_ADDRS["key_item_amount_start"][0] + item, [amount[0][0]], "EWRAM")])

            await asyncio.sleep(0.05)
            total += 0.05
            if write_result:
                total = 1
            if total > 1:
                return False

        return True

    @staticmethod
    async def give_hp_mem(ctx: "BizHawkClientContext") -> bool:
        # First, get the hp amounts we have
        read_result = await read(ctx.bizhawk_ctx, [
            RAM_ADDRS["base_hp"],
            RAM_ADDRS["curr_hp"],
            RAM_ADDRS["max_hp"]
        ])

        base_hp = (read_result[0][1] << 8) + read_result[0][0]
        curr_hp = (read_result[1][1] << 8) + read_result[1][0]
        max_hp = (read_result[2][1] << 8) + read_result[2][0]

        # If Base HP is already 1000, don't give more HP.
        if base_hp == 1000:
            return True

        write_result = False
        total = 0
        while not write_result:
            # Write to the addresses if they haven't changed.
            write_result = await guarded_write(ctx.bizhawk_ctx,
                                               [(RAM_ADDRS["base_hp"][0], int16_to_byte_list_le(base_hp + 20), "EWRAM"),
                                                        (RAM_ADDRS["curr_hp"][0], int16_to_byte_list_le(curr_hp + 20), "EWRAM"),
                                                        (RAM_ADDRS["max_hp"][0], int16_to_byte_list_le(max_hp + 20), "EWRAM")],
                                               [(RAM_ADDRS["base_hp"][0], int16_to_byte_list_le(base_hp), "EWRAM"),
                                                        (RAM_ADDRS["curr_hp"][0], int16_to_byte_list_le(curr_hp), "EWRAM"),
                                                        (RAM_ADDRS["max_hp"][0], int16_to_byte_list_le(max_hp), "EWRAM")])

            await asyncio.sleep(0.05)
            total += 0.05
            if write_result:
                total = 1
            if total > 1:
                return False

        return True

    @staticmethod
    async def give_reg_up(ctx: "BizHawkClientContext", item, xor) -> bool:
        # Determine amount of regmem to give based on itemID
        amount = 0
        if item == 114:
            amount = 1
        elif item == 115:
            amount = 2
        elif item == 116:
            amount = 3

        # Get the regmem we have, amount of the item, and anticheat base
        read_result = await read(ctx.bizhawk_ctx, [RAM_ADDRS["reg_mem"],
                                                   (RAM_ADDRS["key_item_amount_start"][0] + item, 1, "EWRAM"),
                                                   (RAM_ADDRS["key_item_anticheat_base_start"][0] + item, 1, "EWRAM")])

        reg_mem = read_result[0][0]
        item_amount = read_result[1][0]
        anticheat_base = read_result[2][0]

        # If Reg Memory is already 50, don't give more memory.
        if reg_mem == 50:
            return True

        write_result = False
        total = 0
        while not write_result:
            # Write to the addresses if they haven't changed.
            write_result = await guarded_write(ctx.bizhawk_ctx,
                                               [(RAM_ADDRS["reg_mem"][0], [reg_mem + amount], "EWRAM"),
                                                (RAM_ADDRS["key_item_amount_start"][0] + item, [item_amount + 1], "EWRAM"),
                                                (RAM_ADDRS["key_item_anticheat_value_start"][0] + item, [anticheat_base ^ xor], "EWRAM")],
                                               [(RAM_ADDRS["reg_mem"][0], [reg_mem], "EWRAM"),
                                                (RAM_ADDRS["key_item_amount_start"][0] + item, [item_amount], "EWRAM")])

            await asyncio.sleep(0.05)
            total += 0.05
            if write_result:
                total = 1
            if total > 1:
                return False

        return True

    @staticmethod
    async def change_zenny(ctx: "BizHawkClientContext", amount) -> bool:
        # First, get the values of the Zenny and the anticheat addresses
        read_result = await read(ctx.bizhawk_ctx, [
            RAM_ADDRS["zenny_amount"],  # Current state of game (is the player actually in-game?)
            RAM_ADDRS["zenny_anticheat_base"]
        ])

        curr_zenny = int.from_bytes(read_result[0], "little")
        # Don't let the player have more than 999,999z
        new_zenny = min(curr_zenny + amount, 999999)
        anticheat_base = int.from_bytes(read_result[1], "little")
        anticheat_value = new_zenny ^ anticheat_base

        write_result = False
        total = 0
        while not write_result:
            # Write to the address if it hasn't changed
            write_result = await guarded_write(ctx.bizhawk_ctx,
                                               [(RAM_ADDRS["zenny_amount"][0], int32_to_byte_list_le(new_zenny), "EWRAM"),
                                                (RAM_ADDRS["zenny_anticheat_value"][0], int32_to_byte_list_le(anticheat_value), "EWRAM")],
                                               [(RAM_ADDRS["zenny_amount"][0], int32_to_byte_list_le(curr_zenny), "EWRAM"),
                                                (RAM_ADDRS["zenny_anticheat_value"][0], int32_to_byte_list_le(curr_zenny ^ anticheat_base), "EWRAM")])

            await asyncio.sleep(0.05)
            total += 0.05
            if write_result:
                total = 1
            if total > 1:
                return False

        return True

    @staticmethod
    async def change_bugfrags(ctx: "BizHawkClientContext", amount) -> bool:
        # First, get the values of the Bugfrags and the anticheat addresses
        read_result = await read(ctx.bizhawk_ctx, [
            RAM_ADDRS["bugfrag_amount"],
            RAM_ADDRS["bugfrag_anticheat_base"]
        ])

        curr_bugfrag = int.from_bytes(read_result[0], "little")
        # Don't let the player have more than 9,999 bugfrags
        new_bugfrag = min(curr_bugfrag + amount, 9999)
        anticheat_base = int.from_bytes(read_result[1], "little")
        new_anticheat_value = new_bugfrag ^ anticheat_base

        write_result = False
        total = 0
        while not write_result:
            # Write to the address if it hasn't changed
            write_result = await guarded_write(ctx.bizhawk_ctx,
                                               [(RAM_ADDRS["bugfrag_amount"][0], int32_to_byte_list_le(new_bugfrag), "EWRAM"),
                                                (RAM_ADDRS["bugfrag_anticheat_value"][0], int32_to_byte_list_le(new_anticheat_value), "EWRAM")],
                                               [(RAM_ADDRS["bugfrag_amount"][0], int32_to_byte_list_le(curr_bugfrag), "EWRAM"),
                                                (RAM_ADDRS["bugfrag_anticheat_value"][0], int32_to_byte_list_le(curr_bugfrag ^ anticheat_base), "EWRAM")])

            await asyncio.sleep(0.05)
            total += 0.05
            if write_result:
                total = 1
            if total > 1:
                return False

        return True

    async def handle_item_receiving(self, ctx: "BizHawkClientContext", received_index: int) -> None:
        # Read all pending receive items and dump into game ram
        for i in range(len(ctx.items_received) - received_index):
            result = False
            slot = ctx.items_received[received_index + i].player

            # If location is from the current players game
            if slot == self.player_slot:
                location_id = ctx.items_received[received_index + i].location
                if location_id in self.location_by_id:
                    location = self.location_by_id[location_id]

                    # If the location type is not Boss, then we skip receiving the item, as this is handled by the game.
                    if not location.type == LocationType.Boss:
                        await write(ctx.bizhawk_ctx, [(
                            RAM_ADDRS["received_index"][0],
                            [(received_index + i + 1) // 0x100, (received_index + i + 1) % 0x100],
                            "EWRAM",
                        )])
                        break

            item_id = ctx.items_received[received_index + i].item
            item = self.item_by_id[item_id]
            if item.type == ItemType.Chip:
                result = await self.give_chip(ctx, item.itemName)
            elif item.type == ItemType.KeyItem or item.type == ItemType.SubChip:
                if item.itemID == 112:
                    # HP Memory
                    result = await self.give_hp_mem(ctx)
                elif item.itemID in (114, 115, 116):
                    # RegUp1
                    result = await self.give_reg_up(ctx, item.itemID, self.key_item_xor)
                else:
                    result = await self.give_item(ctx, item.itemID, self.key_item_xor)
            elif item.type == ItemType.Program:
                # Programs use the same area of memory as key items, but start at itemID 148
                result = await self.give_item(ctx, programs_to_item_id[item.itemName], self.key_item_xor)
            elif item.type == ItemType.Zenny:
                result = await self.change_zenny(ctx, item.count)
            elif item.type == ItemType.BugFrag:
                result = await self.change_bugfrags(ctx, item.count)

            if not result:
                break
            await display_message(ctx.bizhawk_ctx, "Received item " + item.itemName)
            await write(ctx.bizhawk_ctx, [(
                RAM_ADDRS["received_index"][0],
                [(received_index + i + 1) // 0x100, (received_index + i + 1) % 0x100],
                "EWRAM",
            )])

    async def handle_location_sending(self, ctx: "BizHawkClientContext") -> None:
        # Read all location flags in area and add to pending location checks if updates
        locations_to_read = [self.location_by_id[loc_id] for loc_id in ctx.missing_locations
                             if self.location_by_id[loc_id].flag_byte is not None]
        location_reads = [(loc.flag_byte, 1, "EWRAM") for loc in locations_to_read]
        # Only do location checks if the game state is still 0x04
        loc_bytes = await guarded_read(ctx.bizhawk_ctx, location_reads, [(RAM_ADDRS["game_state"][0], [0x04], "EWRAM")])

        if loc_bytes is not None:
            locs_to_send = [locations_to_read[i].id for i, loc_ram in enumerate(loc_bytes)
                            if loc_ram[0] | locations_to_read[i].flag_mask == loc_ram[0]]

            # Send location checks
            if len(locs_to_send) > 0:
                await ctx.send_msgs([{"cmd": "LocationChecks", "locations": locs_to_send}])

    async def handle_special_items(self, ctx: "BizHawkClientContext") -> None:
        # If we have any of the Cross or BeastOut key items, set the proper flags
        flag_val1 = await read(ctx.bizhawk_ctx, [RAM_ADDRS["transformation_flags1"]])
        new_val1 = flag_val1[0][0]

        flag_val2 = await read(ctx.bizhawk_ctx, [RAM_ADDRS["transformation_flags2"]])
        new_val2 = flag_val2[0][0]

        beastout = await read(ctx.bizhawk_ctx,
                              [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["BeastOut"], 1, "EWRAM")])

        if beastout[0][0] > 0:
            new_val1 = new_val1 | 0x80

        if self.game_version == "gregar":
            heatcross = await read(ctx.bizhawk_ctx,
                                   [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["HeatCross"], 1,
                                     "EWRAM")])
            slashcross = await read(ctx.bizhawk_ctx,
                                    [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["SlashCross"], 1,
                                      "EWRAM")])
            eleccross = await read(ctx.bizhawk_ctx,
                                   [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["ElecCross"], 1,
                                     "EWRAM")])
            erasecross = await read(ctx.bizhawk_ctx,
                                    [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["EraseCross"], 1,
                                      "EWRAM")])
            chargecross = await read(ctx.bizhawk_ctx,
                                     [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["ChargeCross"], 1,
                                       "EWRAM")])

            if heatcross[0][0] > 0:
                new_val1 = new_val1 | 0x20
            if slashcross[0][0] > 0:
                new_val1 = new_val1 | 0x08
            if eleccross[0][0] > 0:
                new_val1 = new_val1 | 0x10
            if erasecross[0][0] > 0:
                new_val1 = new_val1 | 0x04
            if chargecross[0][0] > 0:
                new_val1 = new_val1 | 0x02
        else:
            spoutcross = await read(ctx.bizhawk_ctx,
                                   [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["SpoutCross"], 1,
                                     "EWRAM")])
            tengucross = await read(ctx.bizhawk_ctx,
                                    [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["TenguCross"], 1,
                                      "EWRAM")])
            tomahawkcross = await read(ctx.bizhawk_ctx,
                                   [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["TomahawkCross"], 1,
                                     "EWRAM")])
            groundcross = await read(ctx.bizhawk_ctx,
                                    [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["GroundCross"], 1,
                                      "EWRAM")])
            dustcross = await read(ctx.bizhawk_ctx,
                                     [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["DustCross"], 1,
                                       "EWRAM")])

            if spoutcross[0][0] > 0:
                new_val1 = new_val1 | 0x01
            if tengucross[0][0] > 0:
                new_val2 = new_val2 | 0x40
            if tomahawkcross[0][0] > 0:
                new_val2 = new_val2 | 0x80
            if groundcross[0][0] > 0:
                new_val2 = new_val2 | 0x20
            if dustcross[0][0] > 0:
                new_val2 = new_val2 | 0x10
            
        if not(flag_val1[0][0] == new_val1) or not(flag_val2[0][0] == new_val2):
            await guarded_write(ctx.bizhawk_ctx,[(RAM_ADDRS["transformation_flags1"][0], [new_val1], "EWRAM"),
                                                         (RAM_ADDRS["transformation_flags2"][0], [new_val2], "EWRAM")],
                                                [(RAM_ADDRS["transformation_flags1"][0], flag_val1[0], "EWRAM"),
                                                          (RAM_ADDRS["transformation_flags2"][0], flag_val2[0], "EWRAM")])

        btlrcard = await read(ctx.bizhawk_ctx,
                               [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["BtlrCard"], 1,
                                 "EWRAM")])

        # If we have the BtlrCard, enable the proper flags
        if btlrcard[0][0] > 0:
            battler_machine_flags = await read(ctx.bizhawk_ctx, [RAM_ADDRS["virus_battler_machine_flags"]])
            new_battler_machine_flags = battler_machine_flags[0][0] | 0x0F

            virus_battler_enabled_flag = await read(ctx.bizhawk_ctx, [RAM_ADDRS["virus_battler_enabled_flag"]])
            new_virus_battler_enabled_flag = virus_battler_enabled_flag[0][0] | 0x80

            # Enable flags for Mettaur and Gunner, which are given by default. This isn't technically required, but doing
            # it to be safe
            rare_virus_flags1 = await read(ctx.bizhawk_ctx, [RAM_ADDRS["rare_virus_flags1"]])
            new_rare_virus_flags1 = rare_virus_flags1[0][0] | 0x01

            rare_virus_flags4 = await read(ctx.bizhawk_ctx, [RAM_ADDRS["rare_virus_flags4"]])
            new_rare_virus_flags4 = rare_virus_flags4[0][0] | 0x04

            # Try to set flags if the values have changed
            if (not (battler_machine_flags[0][0] == new_battler_machine_flags) or
                not (virus_battler_enabled_flag[0][0] == new_virus_battler_enabled_flag) or
                not (rare_virus_flags1[0][0] == new_rare_virus_flags1) or
                not (rare_virus_flags4[0][0] == new_rare_virus_flags4)):
                await guarded_write(ctx.bizhawk_ctx, [(RAM_ADDRS["virus_battler_machine_flags"][0], [new_battler_machine_flags], "EWRAM"),
                                                      (RAM_ADDRS["virus_battler_enabled_flag"][0], [new_virus_battler_enabled_flag], "EWRAM"),
                                                      (RAM_ADDRS["rare_virus_flags1"][0], [new_rare_virus_flags1], "EWRAM"),
                                                      (RAM_ADDRS["rare_virus_flags4"][0], [new_rare_virus_flags4], "EWRAM")],
                                    [(RAM_ADDRS["virus_battler_machine_flags"][0], battler_machine_flags[0], "EWRAM"),
                                     (RAM_ADDRS["virus_battler_enabled_flag"][0], virus_battler_enabled_flag[0], "EWRAM"),
                                     (RAM_ADDRS["rare_virus_flags1"][0], rare_virus_flags1[0], "EWRAM"),
                                     (RAM_ADDRS["rare_virus_flags4"][0], rare_virus_flags4[0], "EWRAM")])

        # For each Link Navi Upgrade, set the proper flags
        link_navi_upgrades = await read(ctx.bizhawk_ctx,
                              [(RAM_ADDRS["key_item_amount_start"][0] + SPECIAL_KEY_ITEMS["LkNvUpgd"], 1,
                                "EWRAM")])

        if link_navi_upgrades[0][0] > 0:
            link_navi_upgrade_flags = await read(ctx.bizhawk_ctx, [RAM_ADDRS["link_navi_upgrade_flags"]])
            new_flag_val = 0x00
            match link_navi_upgrades[0][0]:
                case 1:
                    new_flag_val = 0x20
                case 2:
                    new_flag_val = 0x30
                case 3:
                    new_flag_val = 0x38
                case 4:
                    new_flag_val = 0x3C
                case _:
                    new_flag_val = 0x3C

            if link_navi_upgrade_flags[0][0] != new_flag_val:
                await guarded_write(ctx.bizhawk_ctx, [(RAM_ADDRS["link_navi_upgrade_flags"][0], [new_flag_val], "EWRAM")],
                                    [(RAM_ADDRS["link_navi_upgrade_flags"][0], link_navi_upgrade_flags[0], "EWRAM")])



    @staticmethod
    async def check_location_scouted(ctx, location):
        flag_value = await read(ctx.bizhawk_ctx, [(location.hint_flag, 1, "EWRAM")])

        return flag_value[0][0] | location.hint_flag_mask == flag_value[0][0]

    async def handle_scoutable_locations(self, ctx: "BizHawkClientContext") -> None:
        trade_bits = [loc.id for loc in scoutable_locations
                      if await self.check_location_scouted(ctx, loc)]
        scouted_locs = [loc for loc in trade_bits if loc not in self.sent_hints]
        if len(scouted_locs) > 0:
            self.sent_hints.extend(scouted_locs)
            await ctx.send_msgs([{
                "cmd": "LocationScouts",
                "locations": scouted_locs,
                "create_as_hint": 2
            }])

    async def handle_room_change(self, ctx: "BizHawkClientContext", main_area_id, sub_area_id) -> None:
        self.main_area = main_area_id
        self.sub_area = sub_area_id
        # Room sync for poptracker tab tracking
        await ctx.send_msgs([{
            "cmd": "Set",
            "key": f"mmbn6_room_{ctx.team}_{ctx.slot}",
            "default": 0,
            "want_reply": False,
            "operations": [{"operation": "replace", "value": f"{main_area_id:02X} {sub_area_id:02X}"}]
        }])

