import os
import pkgutil

import settings
import typing

from BaseClasses import Item, Tutorial, ItemClassification, Region, \
    LocationProgressType

from worlds.AutoWorld import WebWorld, World

from .Rom import MMBN6GregarProcedurePatch, MMBN6FalzarProcedurePatch, write_tokens, MMBN6PatchData
from .Items import MMBN6Item, ItemData, item_table, all_items, item_frequencies, items_by_id, ItemType, item_groups, \
    gregar_only_items, falzar_only_items
from .Locations import MMBN6Location, all_locations, location_table, location_data_table, \
    requests, location_groups, graveyard_locations, ex_boss_locations, sp_boss_locations, \
    virus_battler_locations, gregar_only_locs, falzar_only_locs
from .GregarLocations import gregar_update_addresses
from .FalzarLocations import falzar_update_addresses
from .Options import MMBN6Options, GameVersion
from .Regions import regions, RegionName
from .Names.ItemName import ItemName
from .Names.LocationName import LocationName
from .Client import MMBN6Client
from worlds.generic.Rules import add_rule


class MMBN6Settings(settings.Group):
    class GregarRomFile(settings.UserFilePath):
        """File name of the MMBN6 Cybeast Gregar US rom"""
        copy_to = "Mega Man Battle Network 6 - Cybeast Gregar.gba"
        description = "MMBN6 Gregar ROM File"
        md5s = [MMBN6GregarProcedurePatch.hash]

    class FalzarRomFile(settings.UserFilePath):
        """File name of the MMBN6 Cybeast Falzar US rom"""
        copy_to = "Mega Man Battle Network 6 - Cybeast Falzar.gba"
        description = "MMBN6 Falzar ROM File"
        md5s = [MMBN6FalzarProcedurePatch.hash]

    gregar_rom_file: GregarRomFile = GregarRomFile(GregarRomFile.copy_to)
    falzar_rom_file: FalzarRomFile = FalzarRomFile(FalzarRomFile.copy_to)


class MMBN6Web(WebWorld):
    theme = "ice"

    setup_en = Tutorial(
        "Multiworld Setup Guide",
        "A guide to setting up the MegaMan Battle Network 6 Randomizer connected to an Archipelago Multiworld.",
        "English",
        "setup_en.md",
        "setup/en",
        ["Risch"]
    )
    tutorials = [setup_en]


class MMBN6World(World):
    """
    Play as Lan and MegaMan to stop the evil organization WWW led by the nefarious
    Dr. Wily in their plans to take over the Net! Collect BattleChips, Customize your Navi,
    and utilize powerful Cross transformations to grow strong enough to take on the greatest
    threat the Internet has ever faced (once again)!
    """
    game = "MegaMan Battle Network 6"
    options_dataclass = MMBN6Options
    options: MMBN6Options
    settings_key = "mmbn6_settings"
    settings: typing.ClassVar[MMBN6Settings]
    topology_present = False

    patch_data: MMBN6PatchData

    item_name_to_id = {name: data.code for name, data in item_table.items()}
    location_name_to_id = {loc_data.name: loc_data.id for loc_data in all_locations}

    excluded_locations: typing.Set[str]
    item_frequencies: typing.Dict[str, int]

    location_name_groups = location_groups
    item_name_groups = item_groups

    web = MMBN6Web()

    def __init__(self, multiworld, player):
        super(MMBN6World, self).__init__(multiworld, player)
        self.patch_data = MMBN6PatchData()

    def generate_early(self) -> None:
        """
        called per player before any items or locations are created. You can set properties on your world here.
        Already has access to player options and RNG.
        """
        self.item_frequencies = item_frequencies.copy()

        self.excluded_locations = set()
        if not self.options.include_jobs:
            self.excluded_locations |= {request.name for request in requests}
        if not self.options.include_graveyard:
            self.excluded_locations |= graveyard_locations
        if not self.options.include_ex_bosses:
            self.excluded_locations |= ex_boss_locations
        if not self.options.include_sp_bosses:
            self.excluded_locations |= sp_boss_locations
        if not self.options.include_virus_battler:
            self.excluded_locations |= virus_battler_locations
        if not self.options.include_bass_bx:
            self.excluded_locations.add(LocationName.Bass_BX)
        if not self.options.include_protoman_fz:
            self.excluded_locations.add(LocationName.ProtoMan_FZ)

    def create_regions(self) -> None:
        """
        called to place player's regions into the MultiWorld's regions list. If it's hard to separate, this can be done
        during generate_early or basic as well.
        """
        name_to_region = {}
        for region_info in regions:
            region = Region(region_info.name, self.player, self.multiworld)
            name_to_region[region_info.name] = region
            for location in region_info.locations:
                loc = MMBN6Location(self.player, location, self.location_name_to_id.get(location, None), region)
                if location in self.excluded_locations:
                    loc.progress_type = LocationProgressType.EXCLUDED
                # Don't include locations from the opposite version
                if self.options.game_version == GameVersion.option_gregar and location in falzar_only_locs:
                    continue
                elif self.options.game_version == GameVersion.option_falzar and location in gregar_only_locs:
                    continue
                region.locations.append(loc)
            self.multiworld.regions.append(region)

        # Regions which contribute to explore score when accessible.
        explore_score_region_names = (
            RegionName.Seaside_Overworld,
            RegionName.Seaside_Cyberworld,
            RegionName.Green_Overworld,
            RegionName.Green_Cyberworld,
            RegionName.Sky_Overworld,
            RegionName.Sky_Cyberworld,
            RegionName.ACDC_Overworld,
            RegionName.ACDC_Cyberworld,
            RegionName.Undernet,
            RegionName.Graveyard,
            RegionName.Expo
        )
        explore_score_regions = [self.get_region(region_name) for region_name in explore_score_region_names]

        # Entrances which use explore score in their logic need to register all the explore score regions as indirect
        # conditions.
        def register_explore_score_indirect_conditions(entrance):
            for explore_score_region in explore_score_regions:
                self.multiworld.register_indirect_condition(explore_score_region, entrance)

        for region_info in regions:
            region = name_to_region[region_info.name]
            for connection in region_info.connections:
                entrance = region.connect(name_to_region[connection])

                # Overworld Regions
                if connection == RegionName.Seaside_Overworld:
                    entrance.access_rule = lambda state: state.has(ItemName.Fish, self.player)
                if connection == RegionName.Green_Overworld:
                    entrance.access_rule = lambda state: state.has(ItemName.AuthData, self.player)
                if connection == RegionName.Sky_Overworld:
                    entrance.access_rule = lambda state: state.has(ItemName.Umbrella, self.player)
                if connection == RegionName.ACDC_Overworld:
                    entrance.access_rule = lambda state: state.has(ItemName.ACDCKyDt, self.player)

                # Cyberworld Regions
                # Central 3 can be reached from Central 1 with KeyData, or any cyberworld with the proper key item
                if connection == RegionName.Central3_and_Underground:
                    entrance.access_rule = lambda state: \
                        state.has(ItemName.KeyData, self.player) or \
                        (state.can_reach_region(RegionName.Seaside_Cyberworld, self.player) and \
                         state.has(ItemName.ToolPrgm, self.player)) or \
                        (state.can_reach_region(RegionName.Green_Cyberworld, self.player) and \
                         state.has(ItemName.CybBrdAx, self.player)) or \
                        (state.can_reach_region(RegionName.Sky_Cyberworld, self.player) and \
                         state.has(ItemName.VacData, self.player)) or \
                        (state.can_reach_region(RegionName.ACDC_Cyberworld, self.player) and \
                         state.has(ItemName.AreaPass, self.player)) or \
                        self.multiworld.register_indirect_condition(self.get_region(RegionName.Seaside_Cyberworld),
                                                                    entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Green_Cyberworld), entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Sky_Cyberworld), entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.ACDC_Cyberworld), entrance)
                if connection == RegionName.Seaside_Cyberworld:
                    entrance.access_rule = lambda state: \
                        (state.can_reach_region(RegionName.Central3_and_Underground, self.player) and
                         state.has(ItemName.ToolPrgm, self.player)) or \
                        state.can_reach_region(RegionName.Seaside_Overworld, self.player)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Central3_and_Underground),
                                                                entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Seaside_Overworld), entrance)
                if connection == RegionName.Green_Cyberworld:
                    entrance.access_rule = lambda state: \
                        (state.can_reach_region(RegionName.Central3_and_Underground, self.player) and
                         state.has(ItemName.CybBrdAx, self.player)) or \
                        state.can_reach_region(RegionName.Green_Overworld, self.player)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Central3_and_Underground),
                                                                entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Green_Overworld), entrance)
                if connection == RegionName.Sky_Cyberworld:
                    entrance.access_rule = lambda state: \
                        (state.can_reach_region(RegionName.Central3_and_Underground, self.player) and
                         state.has(ItemName.VacData, self.player)) or \
                        state.can_reach_region(RegionName.Sky_Overworld, self.player)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Central3_and_Underground),
                                                                entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Sky_Overworld), entrance)
                if connection == RegionName.ACDC_Cyberworld:
                    entrance.access_rule = lambda state: \
                        (state.can_reach_region(RegionName.Central3_and_Underground, self.player) and
                         state.has(ItemName.AreaPass, self.player)) or \
                        state.can_reach_region(RegionName.ACDC_Overworld, self.player)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.Central3_and_Underground),
                                                                entrance)
                    self.multiworld.register_indirect_condition(self.get_region(RegionName.ACDC_Overworld), entrance)
                if connection == RegionName.Undernet:
                    entrance.access_rule = lambda state: self.explore_score(state) > 6 and \
                                                         state.can_reach_region(RegionName.Sky_Cyberworld, self.player)
                    register_explore_score_indirect_conditions(entrance)
                if connection == RegionName.Graveyard:
                    entrance.access_rule = lambda state: self.explore_score(state) > 9 and \
                                                         state.can_reach_region(RegionName.Undernet, self.player) and \
                                                         state.has(ItemName.BatKey, self.player)
                    register_explore_score_indirect_conditions(entrance)
                if connection == RegionName.Expo:
                    entrance.access_rule = lambda state: \
                        state.has(ItemName.StampCrd, self.player)

    def create_items(self) -> None:
        # First add in all progression and useful items
        required_items = []
        for item in all_items:
            # Don't include locations from the opposite version
            if self.options.game_version == GameVersion.option_gregar and item.itemName in falzar_only_items:
                continue
            elif self.options.game_version == GameVersion.option_falzar and item.itemName in gregar_only_items:
                continue
            if item.progression != ItemClassification.filler:
                freq = self.item_frequencies.get(item.itemName, 1)
                required_items += [item.itemName for _ in range(freq)]

        for itemName in required_items:
            self.multiworld.itempool.append(self.create_item(itemName))

        # Then, get a random amount of fillers until we have as many items as we have locations
        filler_items = []
        for item in all_items:
            if item.progression == ItemClassification.filler:
                freq = self.item_frequencies.get(item.itemName, 1)
                filler_items += [item.itemName for _ in range(freq)]

        if self.options.game_version == GameVersion.option_gregar:
            remaining = len(all_locations) - len(required_items) - len(gregar_only_locs)
        else:
            remaining = len(all_locations) - len(required_items) - len(falzar_only_locs)
        for i in range(remaining):
            filler_item_name = self.random.choice(filler_items)
            item = self.create_item(filler_item_name)
            self.multiworld.itempool.append(item)
            filler_items.remove(filler_item_name)

    def set_rules(self) -> None:
        """
        called to set access and item rules on locations and entrances.
        """

        # Set WWW ID requirements
        def has_www_id(state):
            return state.has(ItemName.WWWID, self.player)

        add_rule(self.multiworld.get_location(LocationName.Central_Area_1_BMD_2, self.player), has_www_id)
        add_rule(self.multiworld.get_location(LocationName.Seaside_Area_1_BMD_2, self.player), has_www_id)
        add_rule(self.multiworld.get_location(LocationName.Green_Area_2_BMD_3, self.player), has_www_id)
        add_rule(self.multiworld.get_location(LocationName.ACDC_Area_BMD_2, self.player), has_www_id)

        # Set Rush Food requirements. For now, this just requires Sky Town access
        def has_rush_food(state):
            return state.has(ItemName.Umbrella, self.player)

        add_rule(self.multiworld.get_location(LocationName.Green_Area_2_BMD_2, self.player), has_rush_food)
        add_rule(self.multiworld.get_location(LocationName.Sky_Area_1_BMD_2, self.player), has_rush_food)
        add_rule(self.multiworld.get_location(LocationName.ACDC_Area_BMD_1, self.player), has_rush_food)
        add_rule(self.multiworld.get_location(LocationName.Undernet_1_BMD, self.player), has_rush_food)
        add_rule(self.multiworld.get_location(LocationName.Undernet_Zero_Heel_Navi, self.player), has_rush_food)

        # Set Link Navi requirements (Gregar)
        if self.options.game_version == GameVersion.option_gregar:
            # Rush Food requirement, but also blocked by a Tree
            self.multiworld.get_location(LocationName.Undernet_Zero_BMD_1, self.player).access_rule = \
                lambda state: \
                    state.has(ItemName.Umbrella, self.player) and \
                    (state.has(ItemName.HeatCross, self.player) or \
                     state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player))

            # Fires
            self.multiworld.get_location(LocationName.Sky_Area_2_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.HeatCross, self.player) or
                     state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player))
            # ChargeMan requires player to be able to get SkyBanner, or have VacData
            self.multiworld.get_location(LocationName.Underground_2_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.HeatCross, self.player) or
                     (state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player) and
                      (state.has(ItemName.Umbrella, self.player) or state.has(ItemName.VacData, self.player))))
            self.multiworld.get_location(LocationName.Graveyard_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.HeatCross, self.player) or
                     state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player))

            # Geysers
            # EraseMan requires player to be able to get SkyBanner, or VacData and ToolPrgm
            self.multiworld.get_location(LocationName.Seaside_Area_1_BMD_3, self.player).access_rule = \
                lambda state: \
                    ((state.has(ItemName.EraseCross, self.player) and
                      (state.has(ItemName.Umbrella, self.player) or
                       state.has_all({ItemName.VacData, ItemName.KeyData}, self.player) or
                       state.has_all({ItemName.VacData, ItemName.ToolPrgm}, self.player))) or
                     state.has_all({ItemName.ElecCross, ItemName.Umbrella}, self.player))
            self.multiworld.get_location(LocationName.Undernet_Zero_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.EraseCross, self.player) or
                     state.has_all({ItemName.ElecCross, ItemName.Umbrella}, self.player))
            self.multiworld.get_location(LocationName.Graveyard_PMD_1, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.EraseCross, self.player) or
                     state.has_all({ItemName.ElecCross, ItemName.Umbrella}, self.player))

            # Trees
            self.multiworld.get_location(LocationName.Green_Area_1_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.HeatCross, self.player) or
                     state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player))
            self.multiworld.get_location(LocationName.Sky_1_Brown_Navi, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.HeatCross, self.player) or
                     state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player))
            self.multiworld.get_location(LocationName.Graveyard_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.HeatCross, self.player) or
                     state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player))

            # Cloud
            self.multiworld.get_location(LocationName.Sky_Area_1_PMD, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.EraseCross, self.player) or
                     state.has_all({ItemName.ElecCross, ItemName.Umbrella}, self.player))

            # Tornado
            # ChargeMan requires player to be able to get SkyBanner, or VacData and ToolPrgm
            self.multiworld.get_location(LocationName.Seaside_Area_2_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player) or
                     (state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player) and
                      (state.has(ItemName.Umbrella, self.player) or
                       state.has_all({ItemName.VacData, ItemName.KeyData}, self.player) or
                       state.has_all({ItemName.VacData, ItemName.ToolPrgm}, self.player))))
            self.multiworld.get_location(LocationName.Undernet_Zero_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player) or
                     state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player))
            self.multiworld.get_location(LocationName.Graveyard_BMD_4, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.SlashCross, ItemName.AuthData}, self.player) or
                     state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player))

            # Lab Comp 2 requires EraseCross, or access to Undernet
            self.multiworld.get_location(LocationName.Labs_Comp_2_BMD, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.EraseCross, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))
            self.multiworld.get_location(LocationName.Labs_Comp_2_PMD, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.EraseCross, self.player) or
                    state.can_reach_region(RegionName.Undernet, self.player))

            # Vending Machine Comp requires ChargeCross, or access to Undernet
            self.multiworld.get_location(LocationName.Vending_Machine_Comp_BMD_1, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))
            self.multiworld.get_location(LocationName.Vending_Machine_Comp_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.ChargeCross, ItemName.Fish}, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))

        # Set Link Navi requirements (Falzar)
        if self.options.game_version == GameVersion.option_falzar:
            # GroundMan's class requires VacData to reach Central 2
            self.multiworld.get_location(LocationName.GroundMan_Class, self.player).access_rule = \
                lambda state: state.has(ItemName.VacData, self.player)

            # Rush Food requirement, but also blocked by a Tree
            self.multiworld.get_location(LocationName.Undernet_Zero_BMD_1, self.player).access_rule = \
                lambda state: \
                    state.has(ItemName.Umbrella, self.player) and \
                    (state.has(ItemName.GroundCross, self.player) or \
                     state.has(ItemName.TomahawkCross, self.player))

            # Fires
            self.multiworld.get_location(LocationName.Sky_Area_2_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.SpoutCross, self.player) or
                     state.has_all({ItemName.TenguCross, ItemName.AuthData}, self.player))
            self.multiworld.get_location(LocationName.Underground_2_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.SpoutCross, self.player) or
                     state.has_all({ItemName.TenguCross, ItemName.AuthData}, self.player))
            self.multiworld.get_location(LocationName.Graveyard_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.SpoutCross, self.player) or
                     state.has_all({ItemName.TenguCross, ItemName.AuthData}, self.player))

            # Geysers
            # GroundMan requires player to be able to get SkyBanner, or VacData and ToolPrgm
            self.multiworld.get_location(LocationName.Seaside_Area_1_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.SpoutCross, self.player) or
                    (state.has(ItemName.GroundCross, self.player) and
                      (state.has(ItemName.Umbrella, self.player) or
                       state.has_all({ItemName.VacData, ItemName.KeyData}, self.player) or
                       state.has_all({ItemName.VacData, ItemName.ToolPrgm}, self.player))))
            self.multiworld.get_location(LocationName.Undernet_Zero_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.SpoutCross, self.player) or
                     state.has(ItemName.GroundCross, self.player))
            self.multiworld.get_location(LocationName.Graveyard_PMD_1, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.SpoutCross, self.player) or
                     state.has(ItemName.GroundCross, self.player))

            # Trees
            self.multiworld.get_location(LocationName.Green_Area_1_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.GroundCross, self.player) or
                     state.has_all({ItemName.TomahawkCross, ItemName.Umbrella}, self.player))
            self.multiworld.get_location(LocationName.Sky_1_Brown_Navi, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.GroundCross, self.player) or
                     state.has_all({ItemName.TomahawkCross, ItemName.Umbrella}, self.player))
            self.multiworld.get_location(LocationName.Graveyard_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.GroundCross, self.player) or
                     state.has_all({ItemName.TomahawkCross, ItemName.Umbrella}, self.player))

            # Cloud
            self.multiworld.get_location(LocationName.Sky_Area_1_PMD, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.TomahawkCross, ItemName.Umbrella}, self.player) or
                     state.has_all({ItemName.DustCross, ItemName.Fish}, self.player))

            # Tornado
            # DustMan requires player to be able to get SkyBanner, or VacData and ToolPrgm
            self.multiworld.get_location(LocationName.Seaside_Area_2_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.TenguCross, ItemName.AuthData}, self.player) or
                     (state.has_all({ItemName.DustCross, ItemName.Fish}, self.player) and
                      (state.has(ItemName.Umbrella, self.player) or
                       state.has_all({ItemName.VacData, ItemName.KeyData}, self.player) or
                       state.has_all({ItemName.VacData, ItemName.ToolPrgm}, self.player))))
            self.multiworld.get_location(LocationName.Undernet_Zero_BMD_3, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.TenguCross, ItemName.AuthData}, self.player) or
                     state.has_all({ItemName.DustCross, ItemName.Fish}, self.player))
            self.multiworld.get_location(LocationName.Graveyard_BMD_4, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.TenguCross, ItemName.AuthData}, self.player) or
                     state.has_all({ItemName.DustCross, ItemName.Fish}, self.player))

            # Lab Comp 2 requires GroundCross, or access to Undernet
            self.multiworld.get_location(LocationName.Labs_Comp_2_BMD, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.GroundCross, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))
            self.multiworld.get_location(LocationName.Labs_Comp_2_PMD, self.player).access_rule = \
                lambda state: \
                    (state.has(ItemName.GroundCross, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))

            # Vending Machine Comp requires DustCross, or access to Undernet
            self.multiworld.get_location(LocationName.Vending_Machine_Comp_BMD_1, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.DustCross, ItemName.Fish}, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))
            self.multiworld.get_location(LocationName.Vending_Machine_Comp_BMD_2, self.player).access_rule = \
                lambda state: \
                    (state.has_all({ItemName.DustCross, ItemName.Fish}, self.player) or
                     state.can_reach_region(RegionName.Undernet, self.player))

        # For now, set PMDs to be behind an explore score of 6. Otherwise, PMDs are in logic from the get-go, which
        # can be frustrating with a lot of zenny requirements.
        self.multiworld.get_location(LocationName.ACDC_HP_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Aquarium_HP_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Green_HP_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Sky_HP_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Class_6_2_Comp_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Labs_Comp_2_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Punish_Chair_Comp_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Oxygen_Tank_Comp_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Central_Area_3_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Seaside_Area_3_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Green_Area_1_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Underground_1_PMD_1, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Underground_1_PMD_2, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Sky_Area_1_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.ACDC_Area_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Undernet_1_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Undernet_2_PMD, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6

        # Set EX Bosses to be blocked by explore score to prevent forcing players to fight them right away.
        self.multiworld.get_location(LocationName.BlastMan_EX, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.DiveMan_EX, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.CircusMan_EX, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.JudgeMan_EX, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.CircusMan_EX, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.Colonel_EX, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4

        # Set SP Bosses to be blocked by explore score to prevent forcing players to fight them right away.
        self.multiworld.get_location(LocationName.BlastMan_SP, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.DiveMan_SP, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.CircusMan_SP, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.JudgeMan_SP, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.CircusMan_SP, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Colonel_SP, self.player).access_rule = \
            lambda state: self.explore_score(state) > 6

        # Set Virus Battler checks to require BtlrCard, and have explore score requirements so that different rare
        # viruses are accessible.
        self.multiworld.get_location(LocationName.RoboDog_Comp_Virus_Battler, self.player).access_rule = \
            lambda state: state.has(ItemName.BtlrCard, self.player) and self.explore_score(state) > 1
        self.multiworld.get_location(LocationName.Water_Machine_Comp_Virus_Battler, self.player).access_rule = \
            lambda state: state.has(ItemName.BtlrCard, self.player) and self.explore_score(state) > 3
        self.multiworld.get_location(LocationName.Punish_Chair_Comp_Virus_Battler, self.player).access_rule = \
            lambda state: state.has(ItemName.BtlrCard, self.player) and self.explore_score(state) > 6
        self.multiworld.get_location(LocationName.Oxygen_Tank_Comp_Virus_Battler, self.player).access_rule = \
            lambda state: state.has(ItemName.BtlrCard, self.player) and self.explore_score(state) > 8
        self.multiworld.get_location(LocationName.Central_1_Virus_Battler, self.player).access_rule = \
            lambda state: state.has(ItemName.BtlrCard, self.player) and self.explore_score(state) > 8

        # Bass SP requires defeating Bass first, which means Green Cyberworld access is required.
        self.multiworld.get_location(LocationName.Bass_SP, self.player).access_rule = \
            lambda state: state.can_reach_region(RegionName.Green_Cyberworld, self.player)

        # Bass BX requires defeating Bass and Bass SP first, which means Green Cyberworld and Graveyard access is required.
        self.multiworld.get_location(LocationName.Bass_BX, self.player).access_rule = \
            lambda state: state.can_reach_region(RegionName.Green_Cyberworld, self.player) and state.can_reach_region(RegionName.Graveyard, self.player)

        # Get the player's current possible request points based on accessible locations. This determines if a certain rank
        # is achievable to unlock higher star requests.
        def request_points_possible(state):
            # 1 star jobs
            # Virus Deletion and Find Keepsake always reachable
            request_points = 2

            # If Got a Problem. beatable
            if state.can_reach_region(RegionName.Seaside_Overworld, self.player):
                request_points += 1

            # If Errand Request and Get The Chip! beatable
            if state.can_reach_region(RegionName.Seaside_Cyberworld, self.player):
                # Get The Chip! requires DolThdr1 A
                if state.has(ItemName.DolThdr1_A, self.player):
                    request_points += 1
                # Loan Collection starts in Green HP
                if state.can_reach_region(RegionName.Green_Cyberworld, self.player):
                    request_points += 1

                request_points += 1

            # For Somebody Help!, we force the 10,000z option. To prevent heavy grinding, require Millions or
            # a 100,000z drop
            if state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player):
                request_points += 1

            # If Daughter Worry, Stop Him!, and Diet Goods Money beatable
            if state.can_reach_region(RegionName.Green_Overworld, self.player):
                # Daughter Worry and Diet Goods Money require Seaside Cyberworld access
                if state.can_reach_region(RegionName.Seaside_Cyberworld, self.player):
                    request_points += 2

                request_points += 1

            # If Songwriter beatable
            if state.can_reach_region(RegionName.Sky_Cyberworld, self.player):
                request_points += 1

            # If not Rank B, then can't do more requests
            if request_points < 10:
                return request_points

            # 2 star jobs
            # Juvenile Division always beatable. We also cannot get this far without Green Overworld and Seaside Cyberworld
            # access, so Stand In Recruit and Lumber Merchant are always beatable too
            request_points += 6

            # If For Victory! beatable
            if state.has(ItemName.GunDelS1_C, self.player):
                request_points += 2

            # If Stock Up!, Penguins Ran Away, Update Help, and Do Something! beatable
            # Note: Do Something! requires fire damage, but for simplicity, we just assume you can get those in Robo Control
            if state.can_reach_region(RegionName.Seaside_Overworld, self.player):
                request_points += 8

            # If Buy Which Stock? and Want to Meet Daughter beatable
            if state.can_reach_region(RegionName.Sky_Cyberworld, self.player):
                request_points += 4

            # If Not Enough Member beatable
            if state.has_all({ItemName.Fanfare_Z, ItemName.Discord_S, ItemName.Timpani_T}, self.player):
                request_points += 2

            # If Self Research beatable. Assumed you get PoisSeed P, and have OrderSys to buy second one.
            if state.has_all({ItemName.PoisSeed_P, ItemName.OrderSys, ItemName.Anubis_P}, self.player):
                request_points += 2

            # If not Rank A, then can't do more requests
            if request_points < 25:
                return request_points

            # 3 star jobs
            # We cannot get this far without Green Overworld and Seaside Cyberworld access, so Time Capsule and
            # Road to Soul Battler always beatable
            request_points += 6

            # If Find the Virus!, Can't Open Safe, Track the Criminal, and An Experiment! are beatable
            if state.can_reach_region(RegionName.Seaside_Overworld, self.player):
                request_points += 12

            # If Get the Bad Guy is beatable
            if state.has(ItemName.KeyData, self.player):
                request_points += 3

            # If Official Request beatable
            if state.can_reach_region(RegionName.Sky_Overworld, self.player):
                request_points += 3

            # If not Rank S, then can't do more requests
            if request_points < 35:
                return request_points

            # If Where's My Navi beatable
            if state.can_reach_region(RegionName.Undernet, self.player):
                request_points += 4

            # If One More Time. beatable
            if state.can_reach_region(RegionName.Green_Overworld, self.player) and state.can_reach_region(RegionName.ACDC_Overworld, self.player):
                request_points += 4

            # If SupportChip Pls beatable
            if state.can_reach_region(RegionName.Sky_Overworld, self.player) and \
                    state.has_all({ItemName.Atk_30_star, ItemName.BblWrap_Q, ItemName.Geddon_A, ItemName.Recov80_H}, self.player) and \
                    state.has(ItemName.Discord_S, self.player, 2):
                request_points += 4

            # If Negotiate! beatable
            if state.can_reach_region(RegionName.Sky_Overworld, self.player) and \
                    state.can_reach_region(RegionName.Undernet, self.player) and \
                    state.can_reach_region(RegionName.Green_Overworld, self.player):
                request_points += 4

            # Max number of points: 75
            return request_points

        # Set Job additional area access
        self.multiworld.get_location(LocationName.Errand_Request, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Seaside_Cyberworld, self.player)
        self.multiworld.get_location(LocationName.For_Victory, self.player).access_rule = \
            lambda state: \
                state.has(ItemName.GunDelS1_C, self.player) and \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.JuvenileDiv, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        # For Somebody Help!, we force the 10,000z option. To prevent heavy grinding, require Millions or 100,000z
        self.multiworld.get_location(LocationName.Somebody_Help, self.player).access_rule = \
            lambda state: \
                state.has(ItemName.Millions, self.player) or \
                state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Get_The_Chip, self.player).access_rule = \
            lambda state: \
                state.has(ItemName.DolThdr1_A, self.player)
        self.multiworld.get_location(LocationName.Stock_Up, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.StandIn_Recruit, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.PenguinsRanAway, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.Daughter_Worry, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Seaside_Cyberworld, self.player)
        self.multiworld.get_location(LocationName.Loan_Collection, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Seaside_Cyberworld, self.player)
        self.multiworld.get_location(LocationName.Lumber_Merchant, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Seaside_Cyberworld, self.player) and \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.TimeCpsl, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.DietGood_Money, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Seaside_Cyberworld, self.player) and \
                state.can_reach_region(RegionName.Green_Overworld, self.player)
        self.multiworld.get_location(LocationName.Find_The_Virus, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Seaside_Overworld, self.player) and \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.Buy_Whch_Stock, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.Cant_Open_Safe, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.Get_The_Bad_Guy, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Central3_and_Underground, self.player) and \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.Update_Help, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        # Note: This request requires fire damage, but we assume you can get fire chips from Robo Control in a worst case
        self.multiworld.get_location(LocationName.Do_Something, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.Want_Meet_Dghtr, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Green_Cyberworld, self.player) and \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.Not_Engh_Member, self.player).access_rule = \
            lambda state: \
                state.has_all({ItemName.Fanfare_Z, ItemName.Timpani_T}, self.player) and \
                state.has(ItemName.Discord_S, self.player, 2) and \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.Track_The_Crmnl_1, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.Track_The_Crmnl_2, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.Track_The_Crmnl_3, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        # Pipe Comp requires access to Track the Criminal to open up
        self.multiworld.get_location(LocationName.Pipe_Comp_BMD, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        # To do the PA, we assume you get PoisSeed P, and have OrderSys to buy a second one.
        self.multiworld.get_location(LocationName.Self_Research, self.player).access_rule = \
            lambda state: \
                state.has_all({ItemName.PoisSeed_P, ItemName.OrderSys, ItemName.Anubis_P}, self.player) and \
                request_points_possible(state) >= 10
        self.multiworld.get_location(LocationName.OfficialRequest_1, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.OfficialRequest_2, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.OfficialRequest_3, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.Wheres_My_Navi, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Undernet, self.player) and \
                request_points_possible(state) >= 35
        self.multiworld.get_location(LocationName.One_More_Time, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Green_Overworld, self.player) and \
                state.can_reach_region(RegionName.ACDC_Overworld, self.player) and \
                request_points_possible(state) >= 35
        self.multiworld.get_location(LocationName.SupportChip_Pls, self.player).access_rule = \
            lambda state: \
                state.has_all({ItemName.Atk_30_star, ItemName.BblWrap_Q, ItemName.Geddon_A, ItemName.Recov80_H},
                              self.player) and \
                state.has(ItemName.Discord_S, self.player, 2) and \
                request_points_possible(state) >= 35
        self.multiworld.get_location(LocationName.Negotiate, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Undernet, self.player) and \
                state.can_reach_region(RegionName.Green_Overworld, self.player) and \
                request_points_possible(state) >= 35
        self.multiworld.get_location(LocationName.An_Experiment_1, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.An_Experiment_2, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.An_Experiment_3, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 25
        self.multiworld.get_location(LocationName.RoadToSoulBtlr, self.player).access_rule = \
            lambda state: \
                state.can_reach_region(RegionName.Green_Overworld, self.player) and \
                request_points_possible(state) >= 25

        self.multiworld.get_location(LocationName.ProtoMan_FZ, self.player).access_rule = \
            lambda state: \
                request_points_possible(state) >= 75

        # Set Trade quests
        self.multiworld.get_location(LocationName.Central_Barr100_H_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.Barr100_H, self.player)
        self.multiworld.get_location(LocationName.Aquarium_PnlRetrn_star_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.PnlRetrn_star, self.player)
        self.multiworld.get_location(LocationName.Green_HolyPnl_S_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.HolyPanl_S, self.player)
        self.multiworld.get_location(LocationName.AirCon_AuraHed1_B_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.AuraHed1_B, self.player)
        self.multiworld.get_location(LocationName.Class_1_2_EnergBom_K_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.EnergBom_K, self.player)
        self.multiworld.get_location(LocationName.Aquarium_DublShot_C_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.DublShot_C, self.player)
        self.multiworld.get_location(LocationName.WatrMchn_HiBoomer_V_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.HiBoomer_V, self.player)
        self.multiworld.get_location(LocationName.Sky_GrabRvng_I_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.GrabRvng_I, self.player)
        self.multiworld.get_location(LocationName.ACDC_BigBomb_O_Trade, self.player).access_rule = \
            lambda state: state.has(ItemName.BigBomb_O, self.player)

        # Set Number Traders

        # The first 5 are considered cheap enough to grind for in Central. Robo Control 2 GMDs can drop 450-1200z.
        self.multiworld.get_location(LocationName.Lotto_Code_06, self.player).access_rule = \
            lambda state: self.explore_score(state) > 2
        self.multiworld.get_location(LocationName.Lotto_Code_07, self.player).access_rule = \
            lambda state: self.explore_score(state) > 2
        self.multiworld.get_location(LocationName.Lotto_Code_08, self.player).access_rule = \
            lambda state: self.explore_score(state) > 2
        self.multiworld.get_location(LocationName.Lotto_Code_09, self.player).access_rule = \
            lambda state: self.explore_score(state) > 2
        self.multiworld.get_location(LocationName.Lotto_Code_10, self.player).access_rule = \
            lambda state: self.explore_score(state) > 2
        self.multiworld.get_location(LocationName.Lotto_Code_11, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.Lotto_Code_12, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.Lotto_Code_13, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.Lotto_Code_14, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4
        self.multiworld.get_location(LocationName.Lotto_Code_15, self.player).access_rule = \
            lambda state: self.explore_score(state) > 4

        # After the first 15, require Millions logically.
        self.multiworld.get_location(LocationName.Lotto_Code_16, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_17, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_18, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_19, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_20, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_21, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_22, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_23, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_24, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_25, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)

        self.multiworld.get_location(LocationName.Lotto_Code_26, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_27, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_28, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_29, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_30, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_31, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_32, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_33, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_34, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_35, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)

        self.multiworld.get_location(LocationName.Lotto_Code_36, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_37, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_38, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_39, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_40, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_41, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_42, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_43, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_44, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_45, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)

        self.multiworld.get_location(LocationName.Lotto_Code_46, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_47, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_48, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_49, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_50, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_51, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_52, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_53, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_54, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_55, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)

        self.multiworld.get_location(LocationName.Lotto_Code_56, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_57, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)
        self.multiworld.get_location(LocationName.Lotto_Code_58, self.player).access_rule = \
            lambda state: state.has(ItemName.Millions, self.player) or state.has(ItemName.zenny_100000z, self.player)

        # Gregar requires all 4 Win Cards to be defeatable:
        self.multiworld.get_location(LocationName.Gregar_Defeated, self.player).access_rule = \
            lambda state: \
                state.has_all({ItemName.WinCardA, ItemName.WinCardB, ItemName.WinCardC, ItemName.WinCardD}, self.player)

        # place "Victory" at "Final Boss" and set collection as win condition
        self.multiworld.get_location(LocationName.Gregar_Defeated, self.player) \
            .place_locked_item(self.create_event(ItemName.Victory))
        self.multiworld.completion_condition[self.player] = \
            lambda state: state.has(ItemName.Victory, self.player)

    def generate_output(self, output_directory: str) -> None:
        if self.options.game_version == GameVersion.option_gregar:
            patch = MMBN6GregarProcedurePatch(player=self.player, player_name=self.player_name)
            patch.write_file("base_patch.bsdiff",
                             pkgutil.get_data(__name__, "data/bn6g-ap-patch.bsdiff"))
        else:
            patch = MMBN6FalzarProcedurePatch(player=self.player, player_name=self.player_name)
            patch.write_file("base_patch.bsdiff",
                             pkgutil.get_data(__name__, "data/bn6f-ap-patch.bsdiff"))

        game_version = self.options.game_version.current_key
        self.patch_data.set_game_version(game_version)
        item_hinting = self.options.trade_quest_hinting
        self.patch_data.set_item_hinting(item_hinting)

        write_tokens(self, self.player)
        patch.write_file("token_data.bin", self.patch_data.get_token_bytes())

        # Write output
        out_file_name = self.multiworld.get_out_file_name_base(self.player)
        patch.write(os.path.join(output_directory, f"{out_file_name}{patch.patch_file_ending}"))

    def create_item(self, name: str) -> "Item":
        item = item_table[name]
        return MMBN6Item(item.itemName, item.progression, item.code, self.player)

    def create_event(self, event: str):
        # while we are at it, we can also add a helper to create events
        return MMBN6Item(event, ItemClassification.progression, None, self.player)

    def fill_slot_data(self):
        return self.options.as_dict(
            "game_version",
            "include_jobs",
            "include_graveyard",
            "include_ex_bosses",
            "include_sp_bosses",
            "include_virus_battler",
            "trade_quest_hinting",
            "include_bass_bx",
            "include_protoman_fz"
        )

    def explore_score(self, state):
        """
        Determine roughly how much of the game you can explore to make certain checks not restrict much movement
        """
        score = 0
        if state.can_reach_region(RegionName.Seaside_Overworld, self.player):
            score += 2
        if state.can_reach_region(RegionName.Seaside_Cyberworld, self.player):
            score += 1
        if state.can_reach_region(RegionName.Green_Overworld, self.player):
            score += 1
        if state.can_reach_region(RegionName.Green_Cyberworld, self.player):
            score += 1
        if state.can_reach_region(RegionName.Sky_Overworld, self.player):
            score += 2
        if state.can_reach_region(RegionName.Sky_Cyberworld, self.player):
            score += 1
        if state.can_reach_region(RegionName.ACDC_Overworld, self.player):
            score += 1
        if state.can_reach_region(RegionName.ACDC_Cyberworld, self.player):
            score += 1
        if state.can_reach_region(RegionName.Undernet, self.player):
            score += 1
        if state.can_reach_region(RegionName.Graveyard, self.player):
            score += 1
        if state.can_reach_region(RegionName.Expo, self.player):
            score += 1
        return score
