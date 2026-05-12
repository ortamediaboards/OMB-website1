"""
Расширяет NEXT ART OTF добавлением казахских букв,
которых нет в исходном шрифте: Ә ә Қ қ Ң ң Ғ ғ Ү ү Ұ ұ Ө ө Һ һ.

Каждая новая буква строится из существующего глифа NEXT ART (К, Н, О, Г, У, Х)
плюс простой диакритический штрих, нарисованный программно.
Результат — единый OTF, без переключения на чужой шрифт.
"""
from fontTools.ttLib import TTFont
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.cffLib import TopDict
import os, sys, copy


# (codepoint, base_codepoint, modification_kind, new_glyph_name)
# kinds: 'descender'  — добавить хвост-десцендер у нижнего правого угла (как у Ц)
#        'midbar'     — горизонтальная перекладина по центру (как у Ө)
#        'topbar'     — горизонтальная перекладина сверху (как у Ғ, Ұ)
#        'macron'     — горизонтальная сверху над буквой (как у Ӯ)
#        'identity'   — без изменений (используем базовый глиф; нужно для Һ→Н, Ә→Э и т.п. как заглушка)

LAYOUT = [
    # Қ / қ — К с десцендером
    (0x049A, 0x041A, 'descender', 'Kahook'),
    (0x049B, 0x043A, 'descender', 'kahook'),
    # Ң / ң — Н с десцендером
    (0x04A2, 0x041D, 'descender', 'Endescender'),
    (0x04A3, 0x043D, 'descender', 'endescender'),
    # Ө / ө — О с центральной перекладиной
    (0x04E8, 0x041E, 'midbar',    'Obarred'),
    (0x04E9, 0x043E, 'midbar',    'obarred'),
    # Ғ / ғ — Г с верхней перекладиной (slash)
    (0x0492, 0x0413, 'gstroke',   'Gestroke'),
    (0x0493, 0x0433, 'gstroke',   'gestroke'),
    # Ү / ү — У (упрощённо, NEXT ART сам У уже декоративный)
    (0x04AE, 0x0423, 'identity',  'Ustraight'),
    (0x04AF, 0x0443, 'identity',  'ustraight'),
    # Ұ / ұ — У с верхней перекладиной
    (0x04B0, 0x0423, 'topbar',    'Ustrokestraight'),
    (0x04B1, 0x0443, 'topbar',    'ustrokestraight'),
    # Ә / ә — Э (приближение, форма похожа)
    (0x04D8, 0x042D, 'identity',  'Schwa'),
    (0x04D9, 0x044D, 'identity',  'schwa'),
    # Һ / һ — h приближение (Latin H/h)
    (0x04BA, 0x0048, 'identity',  'Hadescender'),
    (0x04BB, 0x0068, 'identity',  'hadescender'),
]


def get_glyph_bounds(glyph_set, glyph_name):
    """Возвращает (xMin, yMin, xMax, yMax) глифа."""
    pen = BoundsPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    return pen.bounds  # либо None для пустого глифа


def get_advance_width(font, glyph_name):
    return font['hmtx'][glyph_name][0]


def replay_recording(recording, target_pen):
    """Воспроизвести записанные команды пера на другой пен."""
    for op, args in recording:
        getattr(target_pen, op)(*args)


def add_descender(t2_pen, bounds):
    """Десцендер-хвост в нижнем правом углу."""
    if not bounds: return
    xMin, yMin, xMax, yMax = bounds
    # Хвост: толщина ~ 8% ширины, длина ~ 28% высоты, вниз от базовой линии
    w = (xMax - xMin) * 0.10
    h = (yMax - yMin) * 0.28
    x = xMax - w * 1.6
    y_top = yMin + (yMax - yMin) * 0.05  # чуть выше базовой
    y_bot = yMin - h
    # Прямоугольник
    t2_pen.moveTo((x, y_top))
    t2_pen.lineTo((x + w, y_top))
    t2_pen.lineTo((x + w, y_bot))
    t2_pen.lineTo((x, y_bot))
    t2_pen.closePath()


def add_midbar(t2_pen, bounds):
    """Горизонтальная перекладина по центру (для Ө)."""
    if not bounds: return
    xMin, yMin, xMax, yMax = bounds
    h = (yMax - yMin)
    bar_h = h * 0.10
    cy = yMin + h * 0.50
    # Перекладина чуть уже самой буквы
    inset = (xMax - xMin) * 0.20
    t2_pen.moveTo((xMin + inset, cy - bar_h / 2))
    t2_pen.lineTo((xMax - inset, cy - bar_h / 2))
    t2_pen.lineTo((xMax - inset, cy + bar_h / 2))
    t2_pen.lineTo((xMin + inset, cy + bar_h / 2))
    t2_pen.closePath()


def add_topbar(t2_pen, bounds):
    """Горизонтальная перекладина над буквой (для Ұ)."""
    if not bounds: return
    xMin, yMin, xMax, yMax = bounds
    bar_h = (yMax - yMin) * 0.08
    y = yMax + bar_h * 1.5
    inset = (xMax - xMin) * 0.10
    t2_pen.moveTo((xMin + inset, y))
    t2_pen.lineTo((xMax - inset, y))
    t2_pen.lineTo((xMax - inset, y + bar_h))
    t2_pen.lineTo((xMin + inset, y + bar_h))
    t2_pen.closePath()


def add_gstroke(t2_pen, bounds):
    """Косая черта поверх Г (для Ғ) — двигается слева вверху вниз вправо."""
    if not bounds: return
    xMin, yMin, xMax, yMax = bounds
    h = (yMax - yMin)
    bar_h = h * 0.10
    # Простая горизонтальная перекладина в верхней трети
    y = yMin + h * 0.78
    inset = (xMax - xMin) * 0.10
    t2_pen.moveTo((xMin - inset * 0.4, y))
    t2_pen.lineTo((xMax + inset * 0.2, y))
    t2_pen.lineTo((xMax + inset * 0.2, y + bar_h))
    t2_pen.lineTo((xMin - inset * 0.4, y + bar_h))
    t2_pen.closePath()


MODS = {
    'descender': add_descender,
    'midbar':    add_midbar,
    'topbar':    add_topbar,
    'gstroke':   add_gstroke,
    'identity':  None,
}


def patch_font(in_path, out_path):
    print(f"\n=== {os.path.basename(in_path)} ===")
    font = TTFont(in_path)
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()
    glyph_order = font.getGlyphOrder()
    cff = font['CFF '].cff
    top_dict = cff.topDictIndex[0]
    char_strings = top_dict.CharStrings
    private = top_dict.Private
    global_subrs = cff.GlobalSubrs

    # cmap subtable для добавления записей
    cmap_table = font['cmap']
    hmtx = font['hmtx']

    added = 0
    for cp_new, cp_base, kind, new_name in LAYOUT:
        if cp_new in cmap:
            continue  # уже есть
        if cp_base not in cmap:
            print(f"  base U+{cp_base:04X} not found, skipping {new_name}")
            continue
        base_glyph_name = cmap[cp_base]

        # 1) запись траектории базового глифа
        rec = RecordingPen()
        glyph_set[base_glyph_name].draw(rec)

        # 2) границы базового глифа
        bounds = get_glyph_bounds(glyph_set, base_glyph_name)

        # 3) ширина advance
        advance = get_advance_width(font, base_glyph_name)

        # 4) рисуем новый глиф
        t2_pen = T2CharStringPen(advance, glyph_set)
        # сначала базовая буква
        for op, args in rec.value:
            getattr(t2_pen, op)(*args)
        # потом — диакритика
        mod_fn = MODS.get(kind)
        if mod_fn and bounds:
            mod_fn(t2_pen, bounds)

        new_charstring = t2_pen.getCharString(private=private, globalSubrs=global_subrs)

        # 5) подыскиваем уникальное имя глифа
        gname = new_name
        i = 0
        while gname in char_strings:
            i += 1
            gname = f"{new_name}.{i}"

        # 6) добавляем в CFF — нужно расширить charStringsIndex и cмаппинг имён
        idx = len(char_strings.charStringsIndex.items)
        char_strings.charStringsIndex.items.append(new_charstring)
        char_strings.charStrings[gname] = idx

        # 7) glyph order
        font.glyphOrder.append(gname)

        # 8) hmtx
        hmtx.metrics[gname] = (advance, hmtx.metrics[base_glyph_name][1])

        # 9) cmap — все unicode-подтаблицы
        for sub in cmap_table.tables:
            if sub.isUnicode():
                sub.cmap[cp_new] = gname

        added += 1

    print(f"  added {added} glyphs")
    font.save(out_path)
    print(f"  saved: {out_path}")


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    files = ['NextArt-Light.otf','NextArt-Regular.otf','NextArt-SemiBold.otf','NextArt-Bold.otf','NextArt-Heavy.otf']
    for f in files:
        in_path = os.path.join(base_dir, f)
        out_path = os.path.join(base_dir, f.replace('.otf', '-KZ.otf'))
        patch_font(in_path, out_path)


if __name__ == '__main__':
    main()
