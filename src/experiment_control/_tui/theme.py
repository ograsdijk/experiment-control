from textual.theme import Theme

BASE_BACKGROUND = "#090C0A"
SECONDARY_BACKGROUND = "#101510"
RAISED_SURFACE = "#141914"
PRIMARY_TEXT = "#C8C8B4"
SECONDARY_TEXT = "#929786"
MUTED_TEXT = "#707669"
DIM_TEXT = "#50564C"
AMBER = "#D39A32"
BRIGHT_AMBER = "#E6B957"
HEALTHY_GREEN = "#86A94A"
WARNING_ORANGE = "#C97832"
FAULT_RED = "#C84F45"
METADATA_TEAL = "#648F8A"
BORDER_SUBTLE = "#343A33"
BORDER_VISIBLE = "#484E44"

INDUSTRIAL_THEME = Theme(
    name="centrex-industrial",
    primary=AMBER,
    secondary=METADATA_TEAL,
    warning=WARNING_ORANGE,
    error=FAULT_RED,
    success=HEALTHY_GREEN,
    accent=BRIGHT_AMBER,
    foreground=PRIMARY_TEXT,
    background=BASE_BACKGROUND,
    surface=SECONDARY_BACKGROUND,
    panel=RAISED_SURFACE,
    boost=RAISED_SURFACE,
    dark=True,
    luminosity_spread=0.08,
    text_alpha=1.0,
    variables={
        "border": AMBER,
        "border-blurred": BORDER_SUBTLE,
        "border-subtle": BORDER_SUBTLE,
        "border-visible": BORDER_VISIBLE,
        "text-secondary": SECONDARY_TEXT,
        "text-muted": MUTED_TEXT,
        "text-disabled": DIM_TEXT,
        "metadata": METADATA_TEAL,
        "selection-background": "#302815",
        "selection-background-blurred": "#211F16",
        "block-cursor-background": "#45381B",
        "block-cursor-foreground": PRIMARY_TEXT,
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#211F16",
        "block-cursor-blurred-foreground": SECONDARY_TEXT,
        "block-cursor-blurred-text-style": "none",
        "block-hover-background": "#28271C",
        "input-cursor-background": BRIGHT_AMBER,
        "input-cursor-foreground": BASE_BACKGROUND,
        "input-selection-background": "#D39A32 35%",
        "footer-background": RAISED_SURFACE,
        "footer-foreground": MUTED_TEXT,
        "footer-key-background": RAISED_SURFACE,
        "footer-key-foreground": AMBER,
        "footer-description-background": RAISED_SURFACE,
        "footer-description-foreground": MUTED_TEXT,
        "footer-item-background": RAISED_SURFACE,
        "button-color-foreground": BASE_BACKGROUND,
        "button-focus-text-style": "bold",
    },
)
