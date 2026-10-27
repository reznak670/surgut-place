"""Генератор артов «Сургутский плейс»: пишет SVG в assets/src и PNG в assets/.

Запуск: python3 assets/src/build.py  (нужен ImageMagick с librsvg и шрифт Montserrat).
Всё рисуется вручную примитивами SVG — без растровых исходников.
"""

import random
import subprocess
from pathlib import Path

SRC = Path(__file__).resolve().parent
OUT = SRC.parent

# --- Палитра «Полярный вечер»: 4 цвета бренда + их затемнения/осветления ---
C = dict(
    # базовые цвета бренда
    teal="#2DCEBF", blue="#2D3DCE", sky="#2D8DCE", orange="#CE6E2D",
    # производные: только затемнения/осветления базовых (см. docs/brand.md)
    crust="#06091D", mantle="#090C29", base="#0B0F31", horizon="#131A57",   # blue ×0.14/0.20/0.24/0.42
    s0="#0F1546",                                                           # blue ×0.34 — панельки
    s0b="#0A1F2D", river="#0E2A3E", ground="#133B57",                       # sky ×0.22/0.30/0.42
    s1="#164667", s2="#1B557C", ov="#287FB9",                               # sky ×0.50/0.60/0.90
    bridge="#8CC0E3", sub="#B5D6EC", text="#EAF3FA", snow="#EAF3FA",        # sky + белый 45/65/90 %
    warm="#E4AF8B", warm2="#EBC5AB",                                        # orange + белый 45/60 %
)
FONT = "Montserrat, 'Noto Sans', sans-serif"
MONO = "'JetBrainsMono Nerd Font', 'JetBrains Mono', monospace"


def svg(w, h, body, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"<defs>{base_defs()}{defs}</defs>{body}</svg>"
    )


def base_defs():
    return f"""
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{C['crust']}"/><stop offset=".55" stop-color="{C['base']}"/>
  <stop offset="1" stop-color="{C['horizon']}"/></linearGradient>
<linearGradient id="aur" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="{C['teal']}" stop-opacity="0"/>
  <stop offset=".25" stop-color="{C['teal']}" stop-opacity=".85"/>
  <stop offset=".6" stop-color="{C['sky']}" stop-opacity=".9"/>
  <stop offset="1" stop-color="{C['blue']}" stop-opacity="0"/></linearGradient>
<linearGradient id="river" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{C['river']}"/><stop offset="1" stop-color="{C['crust']}"/></linearGradient>
<radialGradient id="glow" cx=".5" cy=".5" r=".5">
  <stop offset="0" stop-color="{C['orange']}" stop-opacity=".6"/>
  <stop offset="1" stop-color="{C['orange']}" stop-opacity="0"/></radialGradient>
<radialGradient id="mglow" cx=".5" cy=".5" r=".5">
  <stop offset="0" stop-color="{C['sky']}" stop-opacity=".5"/>
  <stop offset="1" stop-color="{C['sky']}" stop-opacity="0"/></radialGradient>
<filter id="blur8" filterUnits="userSpaceOnUse" x="-500" y="-500" width="3000" height="2000"><feGaussianBlur stdDeviation="8"/></filter>
<filter id="blur20" filterUnits="userSpaceOnUse" x="-500" y="-500" width="3000" height="2000"><feGaussianBlur stdDeviation="20"/></filter>
"""


def stars(w, h, n, seed=1, ymax=None):
    r = random.Random(seed)
    ymax = ymax or h
    out = []
    for _ in range(n):
        x, y = r.uniform(0, w), r.uniform(0, ymax)
        rad = r.choice([0.8, 1.0, 1.2, 1.6, 2.2])
        op = r.uniform(0.35, 0.95)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad}" fill="{C["snow"]}" opacity="{op:.2f}"/>')
    return "".join(out)


def snow(w, h, n, seed=2, ymin=0):
    r = random.Random(seed)
    out = []
    for _ in range(n):
        x, y = r.uniform(0, w), r.uniform(ymin, h)
        rad = r.uniform(1.2, 3.6)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad:.1f}" fill="{C["snow"]}" opacity="{r.uniform(.4, .9):.2f}"/>')
    return "".join(out)


def aurora(w, y, amp=40, thick=60, op=0.8, seed=3):
    """Две размытые волны северного сияния."""
    r = random.Random(seed)
    def wave(dy, a):
        pts = [f"M -50 {y + dy}"]
        seg = w / 4
        for i in range(4):
            x1 = -50 + seg * i + seg / 2
            pts.append(f"Q {x1:.0f} {y + dy + r.choice([-1, 1]) * a:.0f} {-50 + seg * (i + 1) + 25:.0f} {y + dy}")
        return " ".join(pts)
    return (
        f'<g opacity="{op}"><path d="{wave(0, amp)}" stroke="url(#aur)" stroke-width="{thick}" fill="none" filter="url(#blur20)"/>'
        f'<path d="{wave(thick * .6, amp * .7)}" stroke="url(#aur)" stroke-width="{thick * .45:.0f}" fill="none" filter="url(#blur8)" opacity=".7"/></g>'
    )


def skyline(x0, x1, ground, seed=4, scale=1.0, lit=0.35):
    """Панельки с тёплыми окнами."""
    r = random.Random(seed)
    out = []
    x = x0
    while x < x1:
        bw = r.choice([70, 90, 110, 130]) * scale
        floors = r.choice([5, 9, 9, 12, 16, 17])
        fh = 11 * scale
        bh = floors * fh + 10 * scale
        col = r.choice([C["s0"], C["s0b"], C["mantle"]])
        out.append(f'<rect x="{x:.1f}" y="{ground - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{col}"/>')
        cols = int(bw // (14 * scale))
        for f in range(floors):
            for c in range(cols):
                if r.random() < lit:
                    wc = r.choice([C["warm"], C["warm2"], C["orange"], C["warm"]])
                    out.append(
                        f'<rect x="{x + 6 * scale + c * 14 * scale:.1f}" y="{ground - bh + 8 * scale + f * fh:.1f}" '
                        f'width="{6 * scale:.1f}" height="{5 * scale:.1f}" fill="{wc}" opacity="{r.uniform(.55, 1):.2f}"/>'
                    )
        x += bw + r.choice([4, 8, 14]) * scale
    return "".join(out)


def bridge(px, deck, top, left, right, color=None, w=3, cables=6):
    """Вантовый мост с одним пилоном (вдохновлён Югорским мостом)."""
    color = color or C["text"]
    out = [f'<line x1="{left}" y1="{deck}" x2="{right}" y2="{deck}" stroke="{color}" stroke-width="{w * 2}" stroke-linecap="round"/>']
    for i in range(1, cables + 1):
        k = i / cables
        hy = top + (deck - top) * 0.05 * i
        out.append(f'<line x1="{px}" y1="{hy:.1f}" x2="{px + (right - px) * k:.1f}" y2="{deck}" stroke="{color}" stroke-width="{w * .6:.1f}" opacity=".85"/>')
        out.append(f'<line x1="{px}" y1="{hy:.1f}" x2="{px - (px - left) * k:.1f}" y2="{deck}" stroke="{color}" stroke-width="{w * .6:.1f}" opacity=".85"/>')
    out.append(f'<path d="M {px - w * 2} {deck + w * 6} L {px - w * .8} {top} L {px + w * .8} {top} L {px + w * 2} {deck + w * 6} Z" fill="{color}"/>')
    # опоры
    n = 7
    for i in range(n + 1):
        x = left + (right - left) * i / n
        out.append(f'<rect x="{x - w * .6:.1f}" y="{deck}" width="{w * 1.2:.1f}" height="{w * 6}" fill="{color}" opacity=".7"/>')
    return "".join(out)


def text(x, y, s, size, color=None, weight=800, anchor="start", family=FONT, ls=0, op=1):
    color = color or C["text"]
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}" text-anchor="{anchor}" letter-spacing="{ls}" opacity="{op}">{s}</text>'
    )


def mark(cx, cy, s, bg=True):
    """Знак: вантовый мост с одним пилоном над Обью + тёплая луна + полоса сияния. s — диаметр."""
    k = s / 640
    g = []
    if bg:
        g.append(f'<circle cx="{cx}" cy="{cy}" r="{s / 2}" fill="url(#avbg)"/>')

    def P(x, y):
        return f"{cx + (x - 320) * k:.1f} {cy + (y - 320) * k:.1f}"

    def X(x):
        return f"{cx + (x - 320) * k:.1f}"

    def Y(y):
        return f"{cy + (y - 320) * k:.1f}"

    # сияние: чёткая лента + мягкое свечение
    aur = f"M {P(60, 230)} C {P(170, 130)} {P(300, 250)} {P(420, 170)} S {P(560, 130)} {P(600, 170)}"
    g.append(f'<path d="{aur}" stroke="url(#aur)" stroke-width="{80 * k:.1f}" fill="none" stroke-linecap="round" opacity=".55" filter="url(#blur8)"/>')
    g.append(f'<path d="{aur}" stroke="url(#aur)" stroke-width="{26 * k:.1f}" fill="none" stroke-linecap="round" opacity=".9"/>')
    # луна
    g.append(f'<circle cx="{X(470)}" cy="{Y(250)}" r="{40 * k:.1f}" fill="{C["orange"]}"/>')
    # ванты (веером из верхушки пилона)
    sw = 15 * k
    top = 150
    for ys, xe in ((top + 15, 560), (top + 45, 490), (top + 75, 420)):
        g.append(f'<line x1="{X(270)}" y1="{Y(ys)}" x2="{X(xe)}" y2="{Y(400)}" stroke="{C["snow"]}" stroke-width="{sw:.1f}" stroke-linecap="round"/>')
    for ys, xe in ((top + 25, 90), (top + 60, 160)):
        g.append(f'<line x1="{X(270)}" y1="{Y(ys)}" x2="{X(xe)}" y2="{Y(400)}" stroke="{C["snow"]}" stroke-width="{sw:.1f}" stroke-linecap="round"/>')
    # пилон
    g.append(f'<path d="M {P(248, 455)} L {P(262, top)} L {P(278, top)} L {P(292, 455)} Z" fill="{C["snow"]}"/>')
    # полотно
    g.append(f'<rect x="{X(70)}" y="{Y(392)}" width="{500 * k:.1f}" height="{24 * k:.1f}" rx="{12 * k:.1f}" fill="{C["snow"]}"/>')
    # Обь
    g.append(f'<path d="M {P(110, 490)} Q {P(320, 450)} {P(530, 490)}" stroke="{C["teal"]}" stroke-width="{20 * k:.1f}" fill="none" stroke-linecap="round"/>')
    g.append(f'<path d="M {P(190, 540)} Q {P(320, 515)} {P(450, 540)}" stroke="{C["teal"]}" stroke-width="{14 * k:.1f}" fill="none" stroke-linecap="round" opacity=".5"/>')
    return "".join(g)


AVBG = f"""<radialGradient id="avbg" cx=".5" cy=".35" r=".75">
<stop offset="0" stop-color="{C['horizon']}"/><stop offset=".7" stop-color="{C['base']}"/><stop offset="1" stop-color="{C['crust']}"/></radialGradient>"""


def scene_city(w, h, ground_k=0.78, seed=10, aur_y=None, bridge_on=True, bridge_px=None):
    ground = h * ground_k
    body = [f'<rect width="{w}" height="{h}" fill="url(#sky)"/>', stars(w, h, int(w * h / 4000), seed, ground * .7)]
    body.append(aurora(w, aur_y or h * .22, amp=h * .07, thick=h * .12, seed=seed))
    if bridge_on:
        px = bridge_px or w * .72
        body.append(bridge(px, ground - h * .06, ground - h * .42, w * .42, w * 1.05, color=C["bridge"], w=max(2, h / 260), cables=7))
    body.append(skyline(-20, w * .5, ground, seed=seed + 1, scale=h / 720))
    body.append(f'<rect x="0" y="{ground}" width="{w}" height="{h - ground}" fill="url(#river)"/>')
    # отражения окон
    r = random.Random(seed + 5)
    for _ in range(int(w / 25)):
        x = r.uniform(0, w * .5)
        yy = r.uniform(ground + 8, h - 10)
        body.append(f'<rect x="{x:.0f}" y="{yy:.0f}" width="{r.uniform(10, 40):.0f}" height="2" fill="{C["warm"]}" opacity="{r.uniform(.15, .45):.2f}"/>')
    return "".join(body)


# ------------------------------------------------------------- картинки
def avatar():
    s = 640
    body = mark(320, 320, 640)
    return svg(s, s, body, AVBG)


FADE = f"""<linearGradient id="fadeL" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="{C['crust']}" stop-opacity=".92"/><stop offset=".6" stop-color="{C['crust']}" stop-opacity=".7"/>
<stop offset="1" stop-color="{C['crust']}" stop-opacity="0"/></linearGradient>"""


def description():
    w, h = 640, 360
    body = scene_city(w, h, ground_k=.8, seed=21, aur_y=70)
    body += f'<rect x="0" y="0" width="{w * .62}" height="{h}" fill="url(#fadeL)"/>'
    body += text(32, 150, "Сургутский", 46) + text(32, 202, "плейс", 46, C["orange"])
    body += text(34, 240, "свидания на выходные —", 17, C["text"], 600)
    body += text(34, 264, "готовыми сценариями", 17, C["text"], 600)
    return svg(w, h, body, FADE)


def welcome():
    w, h = 1280, 720
    body = scene_city(w, h, ground_k=.8, seed=31, aur_y=95)
    body += f'<rect x="0" y="0" width="{w * .55}" height="{h}" fill="url(#fadeL)"/>'
    body += text(72, 250, "Сургутский", 92) + text(72, 350, "плейс", 92, C["orange"])
    body += text(76, 420, "Вечера, ради которых", 34, C["text"], 600)
    body += text(76, 464, "стоит остаться", 34, C["text"], 600)
    body += text(78, 540, "61° с. ш. · Обь · вечера на выходные", 22, C["teal"], 500, family=MONO)
    return svg(w, h, body, FADE)


def card_frame(w, h, title, sub, art, seed=40, accent=None):
    """Общий каркас обложек пустых состояний: небо, сияние, иллюстрация справа, текст слева."""
    accent = accent or C["orange"]
    body = [f'<rect width="{w}" height="{h}" fill="url(#sky)"/>', stars(w, h, 120, seed, h * .6)]
    body.append(aurora(w, h * .16, amp=90, thick=70, op=.6, seed=seed))
    body.append(f'<ellipse cx="{w / 2}" cy="{h + 60}" rx="{w * .8}" ry="190" fill="{C["s0"]}"/>')
    body.append(f'<ellipse cx="{w / 2}" cy="{h + 90}" rx="{w * .9}" ry="180" fill="{C["ground"]}"/>')
    body.append(snow(w, h, 70, seed + 1))
    body.append(art)
    body.append(text(80, 300, title[0], 64))
    if len(title) > 1:
        body.append(text(80, 376, title[1], 64, accent))
    for i, line in enumerate(sub):
        body.append(text(82, 450 + i * 42, line, 30, C["sub"], 500))
    return svg(w, h, "".join(body))


def lamp_bench(x, y):
    """Скамейка под фонарём с двумя кружками и сугробом."""
    return f"""
<circle cx="{x + 30}" cy="{y - 285}" r="150" fill="url(#glow)"/>
<rect x="{x + 22}" y="{y - 290}" width="10" height="300" rx="5" fill="{C['s2']}"/>
<path d="M {x + 27} {y - 300} q 0 -30 40 -30" stroke="{C['s2']}" stroke-width="10" fill="none" stroke-linecap="round"/>
<path d="M {x + 52} {y - 330} h 34 l -6 26 h -22 z" fill="{C['warm2']}"/>
<path d="M {x + 60} {y - 302} l -30 80 h 90 z" fill="{C['warm2']}" opacity=".12"/>
<rect x="{x + 70}" y="{y - 120}" width="300" height="20" rx="8" fill="{C['warm']}" opacity=".85"/>
<rect x="{x + 70}" y="{y - 90}" width="300" height="20" rx="8" fill="{C['warm']}" opacity=".85"/>
<rect x="{x + 70}" y="{y - 175}" width="300" height="16" rx="8" fill="{C['warm']}" opacity=".7"/>
<rect x="{x + 70}" y="{y - 150}" width="300" height="16" rx="8" fill="{C['warm']}" opacity=".7"/>
<rect x="{x + 90}" y="{y - 70}" width="14" height="70" fill="{C['s2']}"/><rect x="{x + 336}" y="{y - 70}" width="14" height="70" fill="{C['s2']}"/>
<path d="M {x + 66} {y - 124} q 40 -26 80 -6 q 50 -22 110 0 q 60 -24 118 4 z" fill="{C['snow']}"/>
<path d="M {x + 66} {y - 179} q 70 -18 150 -4 q 80 -16 156 2 z" fill="{C['snow']}"/>
<g transform="translate({x + 175} {y - 175})">
  <rect x="0" y="-46" width="36" height="46" rx="6" fill="{C['orange']}"/>
  <path d="M 36 -36 q 18 0 18 14 q 0 14 -18 14" stroke="{C['orange']}" stroke-width="6" fill="none"/>
  <rect x="58" y="-46" width="36" height="46" rx="6" fill="{C['teal']}"/>
  <path d="M 94 -36 q 18 0 18 14 q 0 14 -18 14" stroke="{C['teal']}" stroke-width="6" fill="none"/>
  <path d="M 10 -56 q -6 -14 4 -26 M 26 -58 q -6 -14 4 -26 M 70 -56 q -6 -14 4 -26" stroke="{C['snow']}" stroke-width="4" fill="none" opacity=".6" stroke-linecap="round"/>
</g>
<path d="M {x - 60} {y + 10} q 120 -50 260 -14 q 140 -40 300 10 z" fill="{C['snow']}" opacity=".9"/>
"""


def empty_weekend():
    return card_frame(1280, 720, ["Выходные", "ещё пустые"], ["Покажем, что было", "на прошлой неделе"], lamp_bench(800, 600), seed=41)


def empty_search():
    # лупа над снегом с лисьими следами
    tracks = []
    r = random.Random(7)
    x, y = 560, 700
    for i in range(10):
        x += 64
        y -= 7 + r.uniform(-2, 2)
        dx = -9 if i % 2 else 9
        tracks.append(
            f'<g transform="translate({x:.0f} {y + dx:.0f}) rotate(-18)" fill="{C["ov"]}" opacity=".9">'
            f'<ellipse cx="0" cy="0" rx="9" ry="11"/><circle cx="-9" cy="-14" r="4"/><circle cx="0" cy="-18" r="4"/>'
            f'<circle cx="9" cy="-14" r="4"/></g>'
        )
    art = (
        "".join(tracks)
        + f"""<g transform="translate(960 300) rotate(-25)">
<circle cx="0" cy="0" r="120" fill="{C['sky']}" opacity=".12"/>
<circle cx="0" cy="0" r="120" fill="none" stroke="{C['bridge']}" stroke-width="26"/>
<path d="M -60 -50 q 30 -40 80 -30" stroke="{C['snow']}" stroke-width="10" fill="none" stroke-linecap="round" opacity=".7"/>
<rect x="-18" y="132" width="36" height="130" rx="18" fill="{C['orange']}"/>
<text x="0" y="40" font-family="{FONT}" font-size="120" font-weight="800" fill="{C['bridge']}" text-anchor="middle">?</text>
</g>"""
    )
    return card_frame(1280, 720, ["Ничего", "не нашлось"], ["Уберите дату или бюджет —", "обычно помогает"], art, seed=42)


def empty_bookings():
    def ticket(tx, ty, rot, col, op=1):
        return (
            f'<g transform="translate({tx} {ty}) rotate({rot})" opacity="{op}">'
            f'<path d="M 0 0 h 300 v 50 a 20 20 0 0 0 0 40 v 50 h -300 v -50 a 20 20 0 0 0 0 -40 z" fill="{col}"/>'
            f'<line x1="220" y1="12" x2="220" y2="128" stroke="{C["crust"]}" stroke-width="4" stroke-dasharray="10 8" opacity=".5"/>'
            f'<rect x="30" y="40" width="150" height="16" rx="8" fill="{C["crust"]}" opacity=".35"/>'
            f'<rect x="30" y="76" width="100" height="16" rx="8" fill="{C["crust"]}" opacity=".25"/>'
            f'<circle cx="262" cy="70" r="16" fill="none" stroke="{C["crust"]}" stroke-width="5" opacity=".45"/></g>'
        )
    art = ticket(760, 250, -12, C["teal"], .9) + ticket(820, 330, 6, C["orange"])
    return card_frame(1280, 720, ["Броней", "пока нет"], ["Самое время", "это исправить"], art, seed=43)


def heart_path(cx, cy, s):
    return (
        f"M {cx} {cy + s * .9} C {cx - s * 1.6} {cy - s * .1} {cx - s * .9} {cy - s * 1.3} {cx} {cy - s * .5} "
        f"C {cx + s * .9} {cy - s * 1.3} {cx + s * 1.6} {cy - s * .1} {cx} {cy + s * .9} Z"
    )


def empty_saved():
    art = f"""
<circle cx="950" cy="360" r="230" fill="url(#mglow)"/>
<path d="{heart_path(950, 360, 150)}" fill="{C['sky']}" opacity=".25" stroke="{C['sky']}" stroke-width="14" stroke-dasharray="26 18" stroke-linecap="round"/>
<path d="M 860 250 q 30 -30 70 -20" stroke="{C['snow']}" stroke-width="12" fill="none" stroke-linecap="round" opacity=".8"/>
<g stroke="{C['snow']}" stroke-width="7" stroke-linecap="round" transform="translate(950 360)">
<line x1="0" y1="-44" x2="0" y2="44"/><line x1="-38" y1="-22" x2="38" y2="22"/><line x1="-38" y1="22" x2="38" y2="-22"/>
<line x1="-10" y1="-34" x2="0" y2="-24"/><line x1="10" y1="-34" x2="0" y2="-24"/>
<line x1="-10" y1="34" x2="0" y2="24"/><line x1="10" y1="34" x2="0" y2="24"/></g>
"""
    return card_frame(1280, 720, ["Здесь", "пока пусто"], ["Жмите «Сохранить» —", "соберём список на зиму"], art, seed=44)


def booking_confirmed():
    art = f"""
<circle cx="950" cy="380" r="260" fill="url(#glow)"/>
<g transform="translate(870 470) rotate(-14)">
  <rect x="-70" y="-170" width="140" height="170" rx="22" fill="{C['orange']}"/>
  <path d="M 70 -140 q 60 0 60 50 q 0 50 -60 50" stroke="{C['orange']}" stroke-width="22" fill="none"/>
  <rect x="-70" y="-170" width="140" height="34" rx="14" fill="{C['snow']}" opacity=".9"/></g>
<g transform="translate(1040 470) rotate(14)">
  <rect x="-70" y="-170" width="140" height="170" rx="22" fill="{C['teal']}"/>
  <path d="M -70 -140 q -60 0 -60 50 q 0 50 60 50" stroke="{C['teal']}" stroke-width="22" fill="none"/>
  <rect x="-70" y="-170" width="140" height="34" rx="14" fill="{C['snow']}" opacity=".9"/></g>
<g stroke="{C['warm2']}" stroke-width="8" stroke-linecap="round">
  <line x1="955" y1="250" x2="955" y2="210"/><line x1="910" y1="262" x2="890" y2="230"/><line x1="1000" y1="262" x2="1020" y2="230"/></g>
<circle cx="1120" cy="210" r="56" fill="{C['teal']}"/>
<path d="M 1092 210 l 20 20 l 36 -40" stroke="{C['crust']}" stroke-width="14" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
"""
    return card_frame(1280, 720, ["Подтверждено,", "ждём вас"], ["Напомним за сутки", "и за 3 часа"], art, seed=45, accent=C["teal"])


def feedback_thanks():
    art = f"""
<g transform="translate(960 360)">
  <circle r="200" fill="url(#mglow)"/>
  <path d="M 0 -150 L 42 -46 L 152 -46 L 64 20 L 98 128 L 0 64 L -98 128 L -64 20 L -152 -46 L -42 -46 Z" fill="{C['orange']}"/>
  <path d="M 0 -110 L 26 -40" stroke="{C['snow']}" stroke-width="10" stroke-linecap="round" opacity=".7"/>
</g>
<g fill="{C['warm']}" opacity=".8">
  <circle cx="760" cy="200" r="8"/><circle cx="1180" cy="170" r="10"/><circle cx="1150" cy="540" r="7"/><circle cx="780" cy="520" r="6"/></g>
"""
    return card_frame(1280, 720, ["Спасибо", "за оценку"], ["Так мы понимаем,", "какие вечера оставить"], art, seed=46, accent=C["warm"])


def placeholder():
    w, h = 800, 600
    body = scene_city(w, h, ground_k=.74, seed=51, aur_y=120, bridge_px=w * .62)
    body += f'<rect width="{w}" height="{h}" fill="{C["crust"]}" opacity=".45"/>'
    body += f'<rect x="{w / 2 - 210}" y="{h / 2 - 70}" width="420" height="140" rx="24" fill="{C["base"]}" opacity=".88" stroke="{C["s1"]}" stroke-width="2"/>'
    body += f'<g transform="translate({w / 2 - 150} {h / 2})" fill="none" stroke="{C["orange"]}" stroke-width="6" stroke-linejoin="round">'
    body += '<rect x="-34" y="-24" width="68" height="50" rx="10"/><circle cx="0" cy="1" r="13"/><path d="M -14 -24 l 6 -10 h 16 l 6 10"/></g>'
    body += text(w / 2 - 95, h / 2 - 6, "Фото скоро", 34) + text(w / 2 - 95, h / 2 + 34, "будет", 34, C["orange"])
    return svg(w, h, body)


JOBS = {
    "avatar": avatar, "description": description, "welcome": welcome,
    "empty_weekend": empty_weekend, "empty_search": empty_search,
    "empty_bookings": empty_bookings, "empty_saved": empty_saved,
    "booking_confirmed": booking_confirmed, "feedback_thanks": feedback_thanks,
    "scenario_placeholder": placeholder,
}

if __name__ == "__main__":
    for name, fn in JOBS.items():
        dst_svg = (OUT if name == "avatar" else SRC) / f"{name}.svg"
        dst_svg.write_text(fn(), encoding="utf-8")
        subprocess.run(["magick", "-background", "none", f"rsvg:{dst_svg}", "-depth", "8", str(OUT / f"{name}.png")], check=True)
        print("ok", name)
