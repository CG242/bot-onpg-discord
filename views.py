import discord

from database import Database
from stats import format_leaderboard, format_player_stats


def _split_long_message(text: str, limit: int = 1990) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return parts


class PlayerNotFoundView(discord.ui.View):
    def __init__(
        self,
        db: Database,
        season_id: int,
        search_text: str,
        requester_id: int,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.season_id = season_id
        self.search_text = search_text
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Créer le joueur", style=discord.ButtonStyle.green)
    async def create_player(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        from player_identity import resolve_or_create_player

        player = resolve_or_create_player(self.db, self.search_text)
        stats = self.db.get_player_stats(player["id"], self.season_id)
        content = (
            f"Joueur **{self.db.player_display_name(player)}** créé.\n\n"
            f"{format_player_stats(stats, self.search_text)}"
        )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=content, view=self)


class SeasonNewConfirmView(discord.ui.View):
    def __init__(
        self,
        db: Database,
        new_name: str,
        start_date,
        requester_id: int,
        active_season: dict | None,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.new_name = new_name
        self.start_date = start_date
        self.requester_id = requester_id
        self.active_season = active_season

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Action réservée.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.green)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        archived, new_id = self.db.start_new_season(self.new_name, self.start_date)
        msg = f"Nouvelle saison **{self.new_name}** créée (id={new_id})."
        if archived:
            msg = (
                f"Saison **{archived.get('name')}** archivée.\n"
                f"{msg}"
            )
        msg += (
            "\nSeuls les scores postés **à partir de la date de début** "
            "seront pris en compte."
        )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=msg, view=self)
        bot = interaction.client
        if interaction.guild and hasattr(bot, "update_leaderboard_message"):
            await bot.update_leaderboard_message(interaction.guild)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.red)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Création de saison annulée.", view=self
        )


class ArchivedSeasonSelect(discord.ui.Select):
    def __init__(self, db: Database, seasons: list[dict], requester_id: int):
        self._db = db
        self._requester_id = requester_id
        options = []
        for s in seasons[:25]:
            label = f"{s.get('name', '?')} (id {s['id']})"[:100]
            desc = f"{s.get('start_date', '?')} → {s.get('end_date', '?')}"[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(s["id"]),
                    description=desc,
                )
            )
        super().__init__(
            placeholder="Choisir une saison archivée…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return
        from stats import format_leaderboard as _fmt_lb

        season_id = int(self.values[0])
        season = self._db.get_season(season_id)
        content = _fmt_lb(
            self._db,
            season_id,
            title=f"CLASSEMENT — {season.get('name', '?')}",
            season_info=season,
        )
        await interaction.response.defer(ephemeral=True)
        parts = _split_long_message(content)
        await interaction.followup.send(parts[0], ephemeral=True)
        for part in parts[1:]:
            await interaction.followup.send(part, ephemeral=True)


class ArchivedSeasonView(discord.ui.View):
    def __init__(self, db: Database, seasons: list[dict], requester_id: int):
        super().__init__(timeout=120)
        self.add_item(ArchivedSeasonSelect(db, seasons, requester_id))


class RegionManageView(discord.ui.View):
    """Sélection joueur + boutons BZ / PN / Retirer."""

    def __init__(
        self,
        db: Database,
        players: list[dict],
        requester_id: int,
        *,
        page: int = 0,
    ):
        super().__init__(timeout=180)
        self.db = db
        self.requester_id = requester_id
        self.all_players = players
        self.page = page
        self.selected_player_id: int | None = None
        self._build_items()

    def _page_players(self) -> list[dict]:
        start = self.page * 25
        return self.all_players[start : start + 25]

    def _build_items(self) -> None:
        chunk = self._page_players()
        if chunk:
            self.add_item(_RegionPlayerPickSelect(chunk, self))
        total_pages = max(1, (len(self.all_players) + 24) // 25)
        if self.page > 0:
            self.add_item(_RegionPageButton("◀ Précédent", self.page - 1, self))
        if self.page + 1 < total_pages:
            self.add_item(_RegionPageButton("Suivant ▶", self.page + 1, self))
        self.add_item(_RegionActionButton("BZ", "BZ", self))
        self.add_item(_RegionActionButton("PN", "PN", self))
        self.add_item(_RegionActionButton("Retirer région", None, self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    async def apply_region(
        self, interaction: discord.Interaction, region: str | None
    ) -> None:
        if not self.selected_player_id:
            await interaction.response.send_message(
                "Sélectionnez d'abord un joueur dans le menu.", ephemeral=True
            )
            return
        player = self.db.get_player_by_id(self.selected_player_id)
        if not player:
            await interaction.response.send_message(
                "Joueur introuvable.", ephemeral=True
            )
            return
        name = self.db.player_display_name(player)
        if region:
            self.db.set_player_region(self.selected_player_id, region)
            msg = f"**{name}** → région **{region}**."
        else:
            self.db.clear_player_region(self.selected_player_id)
            msg = f"Région retirée pour **{name}**."
        await interaction.response.edit_message(content=msg, view=None)
        bot = interaction.client
        if interaction.guild and hasattr(bot, "update_leaderboard_message"):
            await bot.update_leaderboard_message(interaction.guild)


class _RegionActionButton(discord.ui.Button):
    def __init__(self, label: str, region: str | None, parent: RegionManageView):
        style = (
            discord.ButtonStyle.secondary
            if region is None
            else discord.ButtonStyle.primary
        )
        super().__init__(label=label, style=style, row=2)
        self._region = region
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        await self._parent.apply_region(interaction, self._region)


class _RegionPlayerPickSelect(discord.ui.Select):
    def __init__(self, players: list[dict], parent: RegionManageView):
        self._parent = parent
        db = parent.db
        options = []
        for p in players:
            name = db.player_display_name(p)
            reg = (p.get("region") or "—").upper()
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(p["id"]),
                    description=f"Région actuelle : {reg}"[:100],
                )
            )
        super().__init__(
            placeholder="Choisir un joueur…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self._parent.selected_player_id = int(self.values[0])
        player = self._parent.db.get_player_by_id(self._parent.selected_player_id)
        name = self._parent.db.player_display_name(player) if player else "?"
        reg = (player.get("region") or "aucune").upper() if player else "—"
        await interaction.response.send_message(
            f"**{name}** sélectionné (région : {reg}).\n"
            "Cliquez **BZ**, **PN** ou **Retirer région**.",
            ephemeral=True,
        )


class _RegionPageButton(discord.ui.Button):
    def __init__(self, label: str, page: int, parent: RegionManageView):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=1)
        self._page = page
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        new_view = RegionManageView(
            self._parent.db,
            self._parent.all_players,
            self._parent.requester_id,
            page=self._page,
        )
        new_view.selected_player_id = self._parent.selected_player_id
        total = len(self._parent.all_players)
        await interaction.response.edit_message(
            content=(
                f"**Gestion des régions** — {total} joueur(s)\n"
                f"Page {self._page + 1} · sélectionnez un joueur puis BZ ou PN."
            ),
            view=new_view,
        )


class RankManageView(discord.ui.View):
    """Attribution de rang en lot — multi-sélection."""

    def __init__(
        self,
        db: Database,
        players: list[dict],
        tier: str,
        requester_id: int,
        *,
        page: int = 0,
        filter_tier: str | None = None,
    ):
        super().__init__(timeout=180)
        self.db = db
        self.tier = tier
        self.requester_id = requester_id
        self.all_players = players
        self.page = page
        self.filter_tier = filter_tier
        self.selected_ids: set[int] = set()
        self._build_items()

    def _page_players(self) -> list[dict]:
        start = self.page * 25
        return self.all_players[start : start + 25]

    def _build_items(self) -> None:
        chunk = self._page_players()
        if chunk:
            self.add_item(_RankPlayerMultiSelect(chunk, self))
        total_pages = max(1, (len(self.all_players) + 24) // 25)
        if self.page > 0:
            self.add_item(_RankPageButton("◀ Précédent", self.page - 1, self))
        if self.page + 1 < total_pages:
            self.add_item(_RankPageButton("Suivant ▶", self.page + 1, self))
        self.add_item(_RankApplyButton(self))
        self.add_item(_RankSelectAllPageButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    async def apply_rank(self, interaction: discord.Interaction) -> None:
        if not self.selected_ids:
            await interaction.response.send_message(
                "Sélectionnez au moins un joueur.", ephemeral=True
            )
            return
        count = self.db.set_players_rank_bulk(
            list(self.selected_ids), self.tier, manual=True
        )
        names = []
        for pid in list(self.selected_ids)[:5]:
            p = self.db.get_player_by_id(pid)
            if p:
                names.append(self.db.player_display_name(p))
        extra = f" (+{count - 5})" if count > 5 else ""
        msg = (
            f"**{count}** joueur(s) → rang `{self.tier}`.\n"
            f"Ex. : {', '.join(names)}{extra}"
        )
        await interaction.response.edit_message(content=msg, view=None)
        bot = interaction.client
        if interaction.guild and hasattr(bot, "update_leaderboard_message"):
            await bot.update_leaderboard_message(interaction.guild)


class _RankPlayerMultiSelect(discord.ui.Select):
    def __init__(self, players: list[dict], parent: RankManageView):
        self._parent = parent
        db = parent.db
        options = []
        for p in players:
            name = db.player_display_name(p)
            cur = (p.get("tier_rank") or "NR").upper()
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(p["id"]),
                    description=f"Rang actuel : {cur}"[:100],
                )
            )
        super().__init__(
            placeholder=f"Choisir joueur(s) → rang {parent.tier}…",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self._parent.selected_ids.update(int(v) for v in self.values)
        await interaction.response.send_message(
            f"**{len(self.values)}** joueur(s) sélectionné(s) "
            f"(total : {len(self._parent.selected_ids)}).\n"
            f"Cliquez **Attribuer {self._parent.tier}** pour confirmer.",
            ephemeral=True,
        )


class _RankApplyButton(discord.ui.Button):
    def __init__(self, parent: RankManageView):
        super().__init__(
            label=f"Attribuer {parent.tier}",
            style=discord.ButtonStyle.green,
            row=2,
        )
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        await self._parent.apply_rank(interaction)


class _RankSelectAllPageButton(discord.ui.Button):
    def __init__(self, parent: RankManageView):
        super().__init__(
            label="Toute la page",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        for p in self._parent._page_players():
            self._parent.selected_ids.add(p["id"])
        await interaction.response.send_message(
            f"Page sélectionnée — **{len(self._parent.selected_ids)}** joueur(s) au total.\n"
            f"Cliquez **Attribuer {self._parent.tier}**.",
            ephemeral=True,
        )


class _RankPageButton(discord.ui.Button):
    def __init__(self, label: str, page: int, parent: RankManageView):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=1)
        self._page = page
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        new_view = RankManageView(
            self._parent.db,
            self._parent.all_players,
            self._parent.tier,
            self._parent.requester_id,
            page=self._page,
            filter_tier=self._parent.filter_tier,
        )
        new_view.selected_ids = set(self._parent.selected_ids)
        filt = self._parent.filter_tier or "tous"
        await interaction.response.edit_message(
            content=(
                f"**Attribution rang `{self._parent.tier}`** — "
                f"{len(self._parent.all_players)} joueur(s) (filtre : {filt})"
                f"\nPage {self._page + 1} · multi-sélection puis **Attribuer**."
            ),
            view=new_view,
        )


class FusionManageView(discord.ui.View):
    """Fusionner deux joueurs — garder + supprimer."""

    def __init__(
        self,
        db: Database,
        players: list[dict],
        requester_id: int,
        *,
        page: int = 0,
    ):
        super().__init__(timeout=180)
        self.db = db
        self.requester_id = requester_id
        self.all_players = players
        self.page = page
        self.keep_id: int | None = None
        self.drop_id: int | None = None
        self._build_items()

    def _page_players(self) -> list[dict]:
        start = self.page * 25
        return self.all_players[start : start + 25]

    def _build_items(self) -> None:
        chunk = self._page_players()
        if chunk:
            self.add_item(_FusionKeepSelect(chunk, self))
            self.add_item(_FusionDropSelect(chunk, self))
        total_pages = max(1, (len(self.all_players) + 24) // 25)
        if self.page > 0:
            self.add_item(_FusionPageButton("◀", self.page - 1, self))
        if self.page + 1 < total_pages:
            self.add_item(_FusionPageButton("▶", self.page + 1, self))
        self.add_item(_FusionConfirmButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    async def do_merge(self, interaction: discord.Interaction) -> None:
        if not self.keep_id or not self.drop_id:
            await interaction.response.send_message(
                "Sélectionnez le joueur à **garder** et celui à **fusionner**.",
                ephemeral=True,
            )
            return
        if self.keep_id == self.drop_id:
            await interaction.response.send_message(
                "Choisissez deux joueurs différents.", ephemeral=True
            )
            return
        keep = self.db.get_player_by_id(self.keep_id)
        drop = self.db.get_player_by_id(self.drop_id)
        if not keep or not drop:
            await interaction.response.send_message(
                "Joueur introuvable.", ephemeral=True
            )
            return
        self.db.merge_player_into(self.keep_id, self.drop_id)
        season = self.db.get_active_season()
        if season:
            await interaction.client.loop.run_in_executor(
                None, self.db.recalculate_season_elo, season["id"]
            )
        msg = (
            f"Fusion OK : **{self.db.player_display_name(drop)}** "
            f"→ **{self.db.player_display_name(keep)}**"
        )
        await interaction.response.edit_message(content=msg, view=None)
        bot = interaction.client
        if interaction.guild and hasattr(bot, "update_leaderboard_message"):
            await bot.update_leaderboard_message(interaction.guild)


class _FusionKeepSelect(discord.ui.Select):
    def __init__(self, players: list[dict], parent: FusionManageView):
        self._parent = parent
        db = parent.db
        options = [
            discord.SelectOption(
                label=db.player_display_name(p)[:100],
                value=str(p["id"]),
                description="Garder ce profil",
            )
            for p in players
        ]
        super().__init__(
            placeholder="Joueur à GARDER…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self._parent.keep_id = int(self.values[0])
        p = self._parent.db.get_player_by_id(self._parent.keep_id)
        name = self._parent.db.player_display_name(p) if p else "?"
        await interaction.response.send_message(
            f"Profil conservé : **{name}**", ephemeral=True
        )


class _FusionDropSelect(discord.ui.Select):
    def __init__(self, players: list[dict], parent: FusionManageView):
        self._parent = parent
        db = parent.db
        options = [
            discord.SelectOption(
                label=db.player_display_name(p)[:100],
                value=str(p["id"]),
                description="Fusionner dans l'autre",
            )
            for p in players
        ]
        super().__init__(
            placeholder="Joueur à FUSIONNER (supprimé)…",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        self._parent.drop_id = int(self.values[0])
        p = self._parent.db.get_player_by_id(self._parent.drop_id)
        name = self._parent.db.player_display_name(p) if p else "?"
        await interaction.response.send_message(
            f"Profil fusionné : **{name}**", ephemeral=True
        )


class _FusionConfirmButton(discord.ui.Button):
    def __init__(self, parent: FusionManageView):
        super().__init__(
            label="Confirmer fusion",
            style=discord.ButtonStyle.danger,
            row=3,
        )
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        await self._parent.do_merge(interaction)


class _FusionPageButton(discord.ui.Button):
    def __init__(self, label: str, page: int, parent: FusionManageView):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=2)
        self._page = page
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        new_view = FusionManageView(
            self._parent.db,
            self._parent.all_players,
            self._parent.requester_id,
            page=self._page,
        )
        new_view.keep_id = self._parent.keep_id
        new_view.drop_id = self._parent.drop_id
        await interaction.response.edit_message(
            content=(
                f"**Fusion de joueurs** — {len(self._parent.all_players)} joueur(s)\n"
                f"Page {self._page + 1} · garder + fusionner puis **Confirmer**."
            ),
            view=new_view,
        )
