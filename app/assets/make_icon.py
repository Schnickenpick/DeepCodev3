"""Generate the DeepCode app icon (icon.ico + icon.png) from the logo shapes:
a dark rounded tile with an accent terminal-shell window framing a >_ prompt.
Run: python make_icon.py   (regenerate if the logo/accent changes)."""
from PIL import Image, ImageDraw

S = 512                      # master render size
ACCENT = (215, 119, 87, 255) # DeepCode orange (renderer.py default)
BG = (12, 12, 15, 255)       # ink

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded dark tile
pad = 24
d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=96, fill=BG)

# shell window frame
m = 120
fw = 18
d.rounded_rectangle([m, m + 10, S - m, S - m - 10], radius=40, outline=ACCENT, width=fw)
# title bar divider
tb = m + 70
d.line([m + fw, tb, S - m - fw, tb], fill=ACCENT, width=fw)
# title dots
r = 11
for i, cx in enumerate((m + 48, m + 92, m + 136)):
    d.ellipse([cx - r, m + 32 - r, cx + r, m + 32 + r], fill=ACCENT)

# >_ prompt, centered inside the window interior
sw = 20
inner_top = tb + fw
inner_bot = S - m - 10 - fw
inner_left = m + fw + 30
ch = 86                       # chevron arm span
cx = inner_left
cy = (inner_top + inner_bot) // 2 - 20
apex_x = cx + 58
# chevron  >
d.line([cx, cy - ch // 2, apex_x, cy], fill=ACCENT, width=sw, joint="curve")
d.line([cx, cy + ch // 2, apex_x, cy], fill=ACCENT, width=sw, joint="curve")
# underscore  _  (on the prompt baseline, well inside the right border)
us_y = cy + ch // 2
d.line([apex_x + 34, us_y, apex_x + 150, us_y], fill=ACCENT, width=sw)

img.save("icon.png")
# multi-resolution ICO for crisp taskbar/titlebar at every scale
img.save("icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("wrote icon.png + icon.ico")
