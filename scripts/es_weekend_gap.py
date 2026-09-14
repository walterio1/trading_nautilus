"""
El salto de fin de semana del ES, con el COSTE DEL ES.

`weekend_gap.py` lleva COST_PCT = 0.00455 escrito a mano: es un tick del E6 con
el euro a 1.10. Aplicado al ES esa constante esta mal por un factor grande y
cambiante: un tick del ES son 0.25 puntos de indice, o sea 0.0185 % de nominal
con el S&P a 1350 (2008) y 0.0037 % a 6800 (2025). Usar 0.00455 % infla el
"x coste" unas 2.7 veces de media, y mucho mas en los primeros años.

Aqui el coste se estima año a año a partir de la propia serie -el menor
incremento no nulo observado dividido por el precio medio, con el factor del
ajuste por ratio cancelandose en el cociente- y cada fin de semana se enfrenta
al coste de SU año.
"""

import statistics
from collections import defaultdict
from datetime import datetime

DATA = "/mnt/user-data/uploads/trading_nautilus/data/ES_full_1min_continuous_ratio_adjusted.txt"
ENTRY_HORIZONS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360]
MIN_GAP_HOURS = 40.0


def cargar():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        for line in f:
            p = line.split(",")
            if len(p) < 5:
                continue
            s = p[0]
            rows.append((datetime(int(s[:4]), int(s[5:7]), int(s[8:10]),
                                  int(s[11:13]), int(s[14:16])),
                         float(p[1]), float(p[4])))
    return rows


def coste_por_anio(rows):
    grano = defaultdict(list)
    precio = defaultdict(list)
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if a[0].year == b[0].year:
            d = abs(b[2] - a[2])
            if d > 0:
                grano[b[0].year].append(d)
        precio[b[0].year].append(b[2])
    out = {}
    for y, ds in grano.items():
        ds.sort()
        g = ds[max(0, int(0.001 * len(ds)))]
        out[y] = 100.0 * g / (sum(precio[y]) / len(precio[y]))
    return out


def stats(v):
    if len(v) < 2:
        return 0.0, 0.0
    m = statistics.mean(v)
    sd = statistics.stdev(v)
    return m, (m / (sd / len(v) ** 0.5)) if sd else 0.0


def main():
    rows = cargar()
    coste = coste_por_anio(rows)

    idx = []
    for i in range(1, len(rows)):
        mins = (rows[i][0] - rows[i - 1][0]).total_seconds() / 60
        if (mins > MIN_GAP_HOURS * 60
                and rows[i - 1][0].weekday() in (3, 4)
                and rows[i][0].weekday() in (6, 0)):
            idx.append(i)

    recs = []
    for i in idx:
        pre = rows[i - 1][2]
        r = {"fecha": rows[i - 1][0], "gap": (rows[i][1] / pre - 1) * 100}
        ok = True
        for w in ENTRY_HORIZONS:
            j = i - 1 - w
            if j < 0:
                ok = False
                break
            r[f"pre{w}"] = (pre / rows[j][2] - 1) * 100
        if ok:
            recs.append(r)

    print(f"{len(recs)} fines de semana, {rows[0][0]:%Y-%m-%d} a {rows[-1][0]:%Y-%m-%d}")
    print("\nCoste de un tick del ES, estimado de la propia serie")
    print(f"  {'año':<6}{'% de nominal':>14}{'veces el del E6 (0.00455)':>28}")
    for y in sorted(coste):
        print(f"  {y:<6}{coste[y]:>14.5f}{coste[y] / 0.00455:>28.2f}")

    gaps = [r["gap"] for r in recs]
    cst = [coste[r["fecha"].year] for r in recs]
    cmed = sum(cst) / len(cst)
    absmean = statistics.mean([abs(g) for g in gaps])
    m, t = stats(gaps)
    print(f"\nCoste medio ponderado por fin de semana: {cmed:.5f} % "
          f"(el script usaba 0.00455, o sea {cmed / 0.00455:.1f}x menos)")
    print(f"Salto medio |gap| = {absmean:.5f} % = {absmean / cmed:.1f}x el coste real")
    print(f"Acierto de signo necesario: {50 * (1 + cmed / absmean):.1f} %")
    print(f"Deriva del salto: {m:+.5f} % (t={t:+.2f}) "
          f"-> 'corto todos los fines de semana' da {-m / cmed:+.2f}x el coste")

    print("\nDESVANECER los ultimos N minutos del viernes, salir en la apertura")
    h = (f"{'ventana':>9}{'bruto %':>11}{'t':>7}{'1a mitad':>11}{'t':>7}"
         f"{'2a mitad':>11}{'t':>7}{'acierto':>9}{'x coste':>9}{'neto %':>9}")
    print(h)
    print("-" * len(h))
    mitad = len(recs) // 2
    for w in ENTRY_HORIZONS:
        v = [(-1 if r[f"pre{w}"] > 0 else 1) * r["gap"] for r in recs]
        neto = [x - c for x, c in zip(v, cst)]
        mt, tt = stats(v)
        ma, ta = stats(v[:mitad])
        mb, tb = stats(v[mitad:])
        hit = 100 * sum(1 for x in v if x > 0) / len(v)
        print(f"{w:>7} m{mt:>+11.5f}{tt:>+7.2f}{ma:>+11.5f}{ta:>+7.2f}"
              f"{mb:>+11.5f}{tb:>+7.2f}{hit:>8.1f}%{mt / cmed:>8.2f}x"
              f"{statistics.mean(neto):>+9.5f}")

    w = 15
    v = [(-1 if r[f"pre{w}"] > 0 else 1) * r["gap"] for r in recs]
    neto = [x - c for x, c in zip(v, cst)]
    por_anio = defaultdict(list)
    for r, x, c in zip(recs, v, cst):
        por_anio[r["fecha"].year].append(x - c)
    print(f"\nAño a año con ventana de {w} minutos, neto del coste de cada año")
    print(f"  {'año':<6}{'fds':>5}{'neto %/fds':>13}{'neto año %':>13}")
    pos = 0
    for y in sorted(por_anio):
        vs = por_anio[y]
        mm = statistics.mean(vs)
        pos += mm > 0
        print(f"  {y:<6}{len(vs):>5}{mm:>+13.5f}{sum(vs):>+13.3f}")
    anios = (recs[-1]["fecha"] - recs[0]["fecha"]).days / 365.25
    mn, tn = stats(neto)
    print(f"\n  años positivos: {pos} de {len(por_anio)}")
    print(f"  neto por fin de semana: {mn:+.5f} % (t={tn:+.2f})")
    print(f"  neto anual sobre nominal: {mn * len(recs) / anios:+.2f} %")


if __name__ == "__main__":
    main()
