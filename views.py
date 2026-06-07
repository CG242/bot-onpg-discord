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


class RegionSelectView(discord.ui.View):
    """Première étape : choisir la région (BZ ou PN)."""

    def __init__(self, db: Database, requester_id: int):
        super().__init__(timeout=180)
        self.db = db
        self.requester_id = requester_id

    async def _run_checks(self, interaction: discord.Interaction) -> bool:
        """Override to fix AttributeError in newer discord.py versions."""
        return await self.interaction_check(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="BZ", style=discord.ButtonStyle.primary, row=0)
    async def select_bz(self, interaction: discord.Interaction, button: discord.ui.Button):
        players = self.db.list_all_players()
        if not players:
            await interaction.response.send_message(
                "Aucun joueur enregistré.", ephemeral=True
            )
            return
        view = PlayerAssignView(self.db, players, "BZ", self.requester_id)
        await interaction.response.edit_message(
            content=f"**Région BZ** — Sélectionnez les joueurs à assigner :",
            view=view,
        )

    @discord.ui.button(label="PN", style=discord.ButtonStyle.primary, row=0)
    async def select_pn(self, interaction: discord.Interaction, button: discord.ui.Button):
        players = self.db.list_all_players()
        if not players:
            await interaction.response.send_message(
                "Aucun joueur enregistré.", ephemeral=True
            )
            return
        view = PlayerAssignView(self.db, players, "PN", self.requester_id)
        await interaction.response.edit_message(
            content=f"**Région PN** — Sélectionnez les joueurs à assigner :",
            view=view,
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.red, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Annulé.", view=None)


class PlayerAssignView(discord.ui.View):
    """Sélection des joueurs pour une région donnée."""

    def __init__(
        self,
        db: Database,
        players: list[dict],
        region: str,
        requester_id: int,
        *,
        page: int = 0,
    ):
        super().__init__(timeout=180)
        self.db = db
        self.region = region
        self.requester_id = requester_id
        self.all_players = players
        self.page = page
        self.selected_player_ids: set[int] = set()
        self._build_items()

    async def _run_checks(self, interaction: discord.Interaction) -> bool:
        """Override to fix AttributeError in newer discord.py versions."""
        return await self.interaction_check(interaction)

    def _page_players(self) -> list[dict]:
        start = self.page * 8
        return self.all_players[start : start + 8]

    def _build_items(self) -> None:
        chunk = self._page_players()
        for idx, player in enumerate(chunk):
            name = self.db.player_display_name(player)
            reg = (player.get("region") or "—").upper()
            button = _PlayerAssignButton(
                f"{name} ({reg})", player["id"], self, row=idx // 2
            )
            self.add_item(button)
        
        total_pages = max(1, (len(self.all_players) + 7) // 8)
        nav_row = 4
        if self.page > 0:
            self.add_item(_PageButton("◀ Précédent", self.page - 1, self, row=nav_row))
        if self.page + 1 < total_pages:
            self.add_item(_PageButton("Suivant ▶", self.page + 1, self, row=nav_row))
        
        self.add_item(_ConfirmAssignButton(self.region, self, row=nav_row))
        self.add_item(_BackButton(self, row=nav_row))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    def toggle_player(self, player_id: int) -> None:
        if player_id in self.selected_player_ids:
            self.selected_player_ids.remove(player_id)
        else:
            self.selected_player_ids.add(player_id)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if not self.selected_player_ids:
            await interaction.response.send_message(
                "Aucun joueur sélectionné.", ephemeral=True
            )
            return
        
        updated_count = 0
        for player_id in self.selected_player_ids:
            self.db.set_player_region(player_id, self.region)
            updated_count += 1
        
        msg = f"**{updated_count}** joueur(s) → région **{self.region}**."
        
        # Retourner au menu de sélection de région
        view = RegionSelectView(self.db, self.requester_id)
        await interaction.response.edit_message(
            content=f"{msg}\n\nChoisissez une autre région ou annulez :",
            view=view,
        )
        bot = interaction.client
        if interaction.guild and hasattr(bot, "update_leaderboard_message"):
            await bot.update_leaderboard_message(interaction.guild)

    async def go_back(self, interaction: discord.Interaction) -> None:
        view = RegionSelectView(self.db, self.requester_id)
        await interaction.response.edit_message(
            content="Choisissez une région (BZ ou PN) :",
            view=view,
        )


class _PlayerAssignButton(discord.ui.Button):
    def __init__(self, label: str, player_id: int, parent: PlayerAssignView, row: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.player_id = player_id
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        self._parent.toggle_player(self.player_id)
        # Mettre à jour le style pour montrer la sélection
        if self.player_id in self._parent.selected_player_ids:
            self.style = discord.ButtonStyle.success
        else:
            self.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self._parent)


class _ConfirmAssignButton(discord.ui.Button):
    def __init__(self, region: str, parent: PlayerAssignView, row: int):
        super().__init__(label=f"Confirmer {region}", style=discord.ButtonStyle.green, row=row)
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        await self._parent.confirm(interaction)


class _BackButton(discord.ui.Button):
    def __init__(self, parent: PlayerAssignView, row: int):
        super().__init__(label="← Retour", style=discord.ButtonStyle.red, row=row)
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        await self._parent.go_back(interaction)


class _PageButton(discord.ui.Button):
    def __init__(self, label: str, page: int, parent: PlayerAssignView, row: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self._page = page
        self._parent = parent

    async def callback(self, interaction: discord.Interaction):
        new_view = PlayerAssignView(
            self._parent.db,
            self._parent.all_players,
            self._parent.region,
            self._parent.requester_id,
            page=self._page,
        )
        new_view.selected_player_ids = self._parent.selected_player_ids
        await interaction.response.edit_message(view=new_view)


