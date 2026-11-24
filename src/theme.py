"""Light and dark desktop palettes."""

COLORS = {
    "BG": ("#F8F2E6", "#151821"),
    "CARD_BG": ("#FFFDFA", "#202530"),
    "CARD_BORDER": ("#393934", "#667180"),
    "CARD_HOVER": ("#EEE2CE", "#384253"),
    "TEXT": ("#252738", "#F6F0E6"),
    "MUTED": ("#565953", "#C1C8D2"),
    "DIM": ("#62665D", "#ABB4C2"),
    "ACCENT": ("#F7CA65", "#806021"),
    "ACCENT_HOVER": ("#F2B747", "#996D22"),
    "ACCENT_BG": ("#D8EAD8", "#304739"),
    "ORANGE": ("#F2B8CB", "#78415B"),
    "SECONDARY": ("#FFF8EC", "#303643"),
    "LINK": ("#37644C", "#9CDEB6"),
    "ACTIVE": ("#DCD7EF", "#4C4168"),
    "LAVENDER": ("#E9DDF7", "#39324E"),
    "SELECT": ("#E6DDF3", "#41394F"),
    "SELECT_ARROW": ("#D1C2E8", "#514460"),
    "SELECT_HOVER": ("#C4B3DF", "#675879"),
    "FIELD": ("#F1ECDC", "#2B303D"),
    "TRACK": ("#DED8CB", "#353D49"),
    "TRACK_HOVER": ("#A89F91", "#607082"),
    "STOP_TEXT": ("#842F2C", "#FFC2B8"),
    "STOP_BG": ("#FFE2DB", "#513037"),
    "STOP_HOVER": ("#F2B8AD", "#6B3941"),
    "STOP_ACTIVE": ("#712724", "#FFE5DF"),
}

class Palette:
    def select(self, dark):
        self.dark = dark
        for name, values in COLORS.items():
            setattr(self, name, values[int(dark)])

palette = Palette()
palette.select(False)
