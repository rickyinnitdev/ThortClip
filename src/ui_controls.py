"""Shared rounded ttk controls. Each visual style owns a small reusable image set."""
from .theme import palette
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk


def surface(root, fill, border, outside):
    image = Image.new("RGB", (72, 72), outside)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 70, 70), radius=22, fill=fill, outline=border, width=3)
    return ImageTk.PhotoImage(image.resize((24, 24), Image.Resampling.LANCZOS), master=root)


class RoundedButton(ttk.Button):
    def __init__(self, parent, **options):
        self.palette = {k: options.pop(k) for k in ("bg", "fg", "activebackground", "activeforeground", "font")}
        self.palette["anchor"] = options.pop("anchor", "center")
        for key in ("borderwidth", "relief"):
            options.pop(key, None)
        padding = (options.pop("padx", 8), max(4, options.pop("pady", 4)))
        options.setdefault("width", 0)
        super().__init__(parent, padding=padding, takefocus=True, **options)
        self._set_style()

    def _set_style(self):
        root = self._root()
        if not hasattr(root, "_button_styles"):
            root._button_styles = {}
        outside = self.master.cget("bg")
        key = (*self.palette.values(), outside)
        if key not in root._button_styles:
            p = self.palette
            name = f"ThortClip{len(root._button_styles)}"
            style = ttk.Style(root)
            images = [surface(root, p["bg"], palette.CARD_BORDER, outside), surface(root, p["activebackground"], "#434239", outside),
                      surface(root, p["bg"], "#6754AA", outside), surface(root, palette.SECONDARY, "#C3BEB4", outside)]
            element = name + ".surface"
            style.element_create(element, "image", images[0], ("disabled", images[3]),
                                 ("pressed", images[1]), ("focus", images[2]), ("active", images[1]), border=8)
            name += ".TButton"
            style.layout(name, [(element, {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])
            style.configure(name, font=p["font"], foreground=p["fg"], anchor=p["anchor"])
            style.map(name, foreground=[("disabled", palette.DIM), ("active", p["activeforeground"])])
            root._button_styles[key] = (name, images)
        super().configure(style=root._button_styles[key][0])

    def configure(self, cnf=None, **kwargs):
        changed = False
        for key in tuple(kwargs):
            if key in self.palette:
                self.palette[key] = kwargs.pop(key)
                changed = True
        if changed:
            self._set_style()
        if kwargs or cnf is not None or not changed:
            return super().configure(cnf, **kwargs)

    config = configure


def style_dropdowns(root, style):
    prefix = "Dark" if palette.dark else "Light"
    images = [surface(root, palette.CARD_BG, palette.CARD_BORDER, palette.BG), surface(root, palette.SELECT, "#715C99", palette.BG)]
    chevron = Image.new("RGBA", (60, 48))
    ImageDraw.Draw(chevron).line([(5, 18), (17, 30), (29, 18)], fill=palette.TEXT, width=5)
    arrow = ImageTk.PhotoImage(chevron.resize((20, 16), Image.Resampling.LANCZOS), master=root)
    if prefix + ".combo" not in style.element_names():
        if not hasattr(root, "_combo_images"):
            root._combo_images = {}
        root._combo_images[prefix] = [*images, arrow]
        style.element_create(prefix + ".chevron", "image", arrow)
        style.element_create(prefix + ".combo", "image", images[0], ("focus", images[1]), ("active", images[1]), border=8)
    style.layout("TCombobox", [(prefix + ".combo", {"sticky": "nswe", "children": [
        (prefix + ".chevron", {"side": "right"}),
        ("Combobox.padding", {"sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]})])
    style.configure("TCombobox", padding=(9, 5), arrowsize=14, arrowcolor="#c6d9df", background=palette.CARD_BG)
    style.map("TCombobox", fieldbackground=[("readonly", palette.CARD_BG)], foreground=[("readonly", palette.TEXT)],
              selectbackground=[("readonly", palette.CARD_BG)], selectforeground=[("readonly", palette.TEXT)])
    for prop, value in dict(background=palette.CARD_BG, foreground=palette.TEXT, selectBackground=palette.ACTIVE,
                            selectForeground=palette.TEXT, font="{Segoe UI} 10", borderWidth=0).items():
        root.option_add("*TCombobox*Listbox." + prop, value)
