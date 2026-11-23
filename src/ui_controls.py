"""Shared rounded ttk controls. Each visual style owns a small reusable image set."""
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
            name = f"Medal{len(root._button_styles)}"
            style = ttk.Style(root)
            images = [surface(root, p["bg"], p["bg"], outside), surface(root, p["activebackground"], "#74879a", outside),
                      surface(root, p["bg"], "#a6ffe0", outside), surface(root, "#202631", "#333d49", outside)]
            element = name + ".surface"
            style.element_create(element, "image", images[0], ("disabled", images[3]),
                                 ("pressed", images[1]), ("focus", images[2]), ("active", images[1]), border=8)
            name += ".TButton"
            style.layout(name, [(element, {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])
            style.configure(name, font=p["font"], foreground=p["fg"], anchor=p["anchor"])
            style.map(name, foreground=[("disabled", "#929cab"), ("active", p["activeforeground"])])
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
    images = [surface(root, "#222c39", "#435062", "#141b24"), surface(root, "#273542", "#60dfb3", "#141b24")]
    chevron = Image.new("RGBA", (60, 48))
    ImageDraw.Draw(chevron).line([(5, 18), (17, 30), (29, 18)], fill="#c8d8e4", width=5)
    arrow = ImageTk.PhotoImage(chevron.resize((20, 16), Image.Resampling.LANCZOS), master=root)
    root._combo_images = [*images, arrow]
    style.element_create("Medal.chevron", "image", arrow)
    style.element_create("Medal.combo", "image", images[0], ("focus", images[1]), ("active", images[1]), border=8)
    style.layout("TCombobox", [("Medal.combo", {"sticky": "nswe", "children": [
        ("Medal.chevron", {"side": "right"}),
        ("Combobox.padding", {"sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]})])
    style.configure("TCombobox", padding=(9, 5), arrowsize=14, arrowcolor="#c6d9df", background="#222c39")
    style.map("TCombobox", fieldbackground=[("readonly", "#222c39")], foreground=[("readonly", "#edf4fa")],
              selectbackground=[("readonly", "#222c39")], selectforeground=[("readonly", "#edf4fa")])
    for prop, value in dict(background="#222c39", foreground="#eef4fa", selectBackground="#315c51",
                            selectForeground="#ffffff", font="{Segoe UI} 10", borderWidth=0).items():
        root.option_add("*TCombobox*Listbox." + prop, value)
