import discord

from database import Database
from player_resolver import get_or_create_from_text, link_player_to_discord
from stats import format_player_stats


class PlayerNotFoundView(discord.ui.View):
    """CAS 3 : joueur introuvable → créer OU lier à un Discord."""

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
        player = get_or_create_from_text(self.db, self.search_text)
        stats = self.db.get_player_stats(player["id"], self.season_id)
        content = format_player_stats(stats, self.search_text)
        content = (
            f"Joueur **{self.db.player_display_name(player)}** créé.\n\n{content}"
        )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Lier à un Discord", style=discord.ButtonStyle.blurple)
    async def start_link(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        view = LinkUserSelectView(
            self.db, self.season_id, self.search_text, self.requester_id
        )
        await interaction.response.edit_message(
            content=(
                f"Sélectionnez le compte Discord à lier au pseudo **{self.search_text}** :"
            ),
            view=view,
        )


class LinkUserSelectView(discord.ui.View):
    def __init__(
        self, db: Database, season_id: int, search_text: str, requester_id: int
    ):
        super().__init__(timeout=120)
        self.db = db
        self.season_id = season_id
        self.search_text = search_text
        self.requester_id = requester_id
        self.add_item(LinkUserSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True


class LinkUserSelect(discord.ui.UserSelect):
    def __init__(self, parent_view: LinkUserSelectView):
        super().__init__(
            placeholder="Choisir un membre Discord…",
            min_values=1,
            max_values=1,
        )
        self._parent = parent_view

    async def callback(self, interaction: discord.Interaction):
        user = self.values[0]
        player = self._parent.db.find_player_by_name(
            self._parent.search_text, self._parent.season_id
        )
        if not player:
            player = get_or_create_from_text(
                self._parent.db, self._parent.search_text, user.id
            )
        else:
            link_player_to_discord(
                self._parent.db, player["id"], user.id, force=True
            )
            player = self._parent.db.get_player_by_id(player["id"])

        stats = self._parent.db.get_player_stats(player["id"], self._parent.season_id)
        content = format_player_stats(stats, self._parent.search_text)
        content = (
            f"**{self._parent.db.player_display_name(player)}** "
            f"lié à <@{user.id}>.\n\n{content}"
        )
        await interaction.response.edit_message(content=content, view=None)


class RegionManageView(discord.ui.View):
    """Sélection de joueurs pour ajouter/retirer une région."""

    PAGE_SIZE = 25

    def __init__(
        self,
        db: Database,
        region: str | None,
        action: str,
        players: list[dict],
        requester_id: int,
        page: int = 0,
    ):
        super().__init__(timeout=180)
        self.db = db
        self.region = region
        self.action = action
        self.players = players
        self.requester_id = requester_id
        self.page = page
        self._build()

    def _page_players(self) -> list[dict]:
        start = self.page * self.PAGE_SIZE
        return self.players[start : start + self.PAGE_SIZE]

    def _build(self) -> None:
        self.clear_items()
        chunk = self._page_players()
        if chunk:
            self.add_item(RegionPlayerSelect(self, chunk))

        total_pages = max(1, (len(self.players) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self.page > 0:
            self.add_item(PrevPageButton(self))
        if self.page < total_pages - 1:
            self.add_item(NextPageButton(self))
        if self.action == "ajouter" and self.players:
            self.add_item(ApplyAllButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette action ne vous concerne pas.", ephemeral=True
            )
            return False
        return True

    async def apply(self, interaction: discord.Interaction, player_ids: list[int]):
        if self.action == "ajouter":
            for pid in player_ids:
                self.db.set_player_region(pid, self.region)
            msg = f"**{len(player_ids)}** joueur(s) assigné(s) à la région **{self.region}**."
        else:
            for pid in player_ids:
                self.db.clear_player_region(pid)
            msg = f"Région retirée pour **{len(player_ids)}** joueur(s)."

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=msg, view=self)
        bot = interaction.client
        if interaction.guild and hasattr(bot, "update_leaderboard_message"):
            await bot.update_leaderboard_message(interaction.guild)


class RegionPlayerSelect(discord.ui.Select):
    def __init__(self, owner: RegionManageView, players: list[dict]):
        options = []
        used_labels: set[str] = set()
        for p in players[:25]:
            region_tag = f" [{p.get('region')}]" if p.get("region") else ""
            base = owner.db.player_display_name(p)
            label = f"{base}{region_tag}"[:100]
            if label in used_labels:
                label = f"{base} (#{p['id']})"[:100]
            used_labels.add(label)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(p["id"]),
                    description=f"Joueur #{p['id']}"[:100],
                )
            )
        if not options:
            options.append(
                discord.SelectOption(
                    label="Aucun joueur",
                    value="0",
                    description="Liste vide",
                )
            )
        super().__init__(
            placeholder="Choisir un ou plusieurs joueurs…",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RegionManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez `/region-ajouter`.", ephemeral=True
            )
            return
        ids = [int(v) for v in self.values if v != "0"]
        if not ids:
            await interaction.response.send_message(
                "Aucun joueur sélectionnable.", ephemeral=True
            )
            return
        await self.view.apply(interaction, ids)


class PrevPageButton(discord.ui.Button):
    def __init__(self, owner: RegionManageView):
        super().__init__(label="◀ Page préc.", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RegionManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez la commande.", ephemeral=True
            )
            return
        self.view.page -= 1
        self.view._build()
        await interaction.response.edit_message(view=self.view)


class NextPageButton(discord.ui.Button):
    def __init__(self, owner: RegionManageView):
        super().__init__(label="Page suiv. ▶", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RegionManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez la commande.", ephemeral=True
            )
            return
        self.view.page += 1
        self.view._build()
        await interaction.response.edit_message(view=self.view)


class ApplyAllButton(discord.ui.Button):
    def __init__(self, owner: RegionManageView):
        super().__init__(label="Tous les joueurs", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RegionManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez la commande.", ephemeral=True
            )
            return
        ids = [p["id"] for p in self.view.players]
        await self.view.apply(interaction, ids)


class RankManageView(discord.ui.View):
    """Admin : attribuer un rang (tier) à un ou plusieurs joueurs."""

    PAGE_SIZE = 25

    def __init__(
        self,
        db: Database,
        tier: str,
        players: list[dict],
        requester_id: int,
        page: int = 0,
    ):
        super().__init__(timeout=180)
        self.db = db
        self.tier = tier
        self.players = players
        self.requester_id = requester_id
        self.page = page
        self._build()

    def _page_players(self) -> list[dict]:
        start = self.page * self.PAGE_SIZE
        return self.players[start : start + self.PAGE_SIZE]

    def _build(self) -> None:
        self.clear_items()
        chunk = self._page_players()
        if chunk:
            self.add_item(RankPlayerSelect(self, chunk))
        total_pages = max(1, (len(self.players) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self.page > 0:
            self.add_item(RankPrevPageButton(self))
        if self.page < total_pages - 1:
            self.add_item(RankNextPageButton(self))
        if self.players:
            self.add_item(RankApplyAllButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Action réservée.", ephemeral=True
            )
            return False
        return True

    async def apply(self, interaction: discord.Interaction, player_ids: list[int]):
        from ranking import elo_for_tier

        for pid in player_ids:
            self.db.set_player_rank(pid, self.tier, manual=True)
        elo = elo_for_tier(self.tier)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=(
                f"**{len(player_ids)}** joueur(s) → rang **{self.tier}** "
                f"({elo} points de base). Les points évolueront avec les matchs."
            ),
            view=self,
        )


class RankPlayerSelect(discord.ui.Select):
    def __init__(self, owner: RankManageView, players: list[dict]):
        options = []
        used_labels: set[str] = set()
        for p in players[:25]:
            tier = p.get("tier_rank") or "NR"
            base = owner.db.player_display_name(p)
            label = base[:100]
            if label in used_labels:
                label = f"{base} (#{p['id']})"[:100]
            used_labels.add(label)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(p["id"]),
                    description=f"Rang actuel : {tier}"[:100],
                )
            )
        super().__init__(
            placeholder="Choisir joueur(s)…",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RankManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez `/rang-attribuer`.", ephemeral=True
            )
            return
        await self.view.apply(interaction, [int(v) for v in self.values])


class RankPrevPageButton(discord.ui.Button):
    def __init__(self, owner: RankManageView):
        super().__init__(label="◀", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RankManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez la commande.", ephemeral=True
            )
            return
        self.view.page -= 1
        self.view._build()
        await interaction.response.edit_message(view=self.view)


class RankNextPageButton(discord.ui.Button):
    def __init__(self, owner: RankManageView):
        super().__init__(label="▶", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RankManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez la commande.", ephemeral=True
            )
            return
        self.view.page += 1
        self.view._build()
        await interaction.response.edit_message(view=self.view)


class RankApplyAllButton(discord.ui.Button):
    def __init__(self, owner: RankManageView):
        super().__init__(label="Tous", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(self.view, RankManageView):
            await interaction.response.send_message(
                "Menu expiré — relancez la commande.", ephemeral=True
            )
            return
        await self.view.apply(
            interaction, [p["id"] for p in self.view.players]
        )
