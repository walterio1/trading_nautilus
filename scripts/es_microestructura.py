"""
Microestructura del ES frente a la del E6: ¿hay reversion que desvanecer?

Replica las tres cifras con las que el memorandum caracterizo el techo del
intradia en el euro, para poder compararlas una a una:

    autocorrelacion de lag 1 en barras de 15 min     E6: -0.0213
    ratios de varianza VR(q)                         E6: 0.948 - 0.978
    regla ingenua de desvanecer la barra previa      E6: 0.00255 %/op = 0.56x coste

EL COSTE NO ES CONSTANTE, Y EN EL ES MENOS QUE EN EL EURO. Un tick del ES son
0.25 puntos de indice: con el S&P a 1400 eso es 0.0179 % de nominal y a 6800 es
0.0037 %, un rango de casi 5x a lo largo de la muestra (en el E6 el rango era
1.7x). Tratarlo como una constante mezclaria epocas incomparables.

Como se resuelve sin el fichero sin ajustar: la serie esta ajustada por RATIO,
asi que todo -precios e incrementos- esta multiplicado por el mismo factor en
cada epoca. El menor incremento no nulo observado en un tramo es por tanto
tick_real * factor, y el precio observado es precio_real * factor: su cociente
es exactamente tick_real / precio_real, el coste en % de nominal de aquel
momento, con el factor cancelado. Se estima por año y con eso se compara la
ventaja contra el coste DEL MISMO año, nunca contra una media global.
"""

import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV = str(DATA_DIR / "ES_15min.csv")


def cargar(max_gap=None):
    ts, closes, fills, gaps = [], [], [], []
    with open(CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            g = int(row["fill_gap_min"])
            if max_gap is not None and g > max_gap:
                continue
            ts.append(row["timestamp"])
            closes.append(float(row["close"]))
            fills.append(float(row["fill_price"]))
            gaps.append(g)
    return ts, closes, fills, gaps


def autocorr(x, lag):
    n = len(x) - lag
    m = sum(x) / len(x)
    num = sum((x[i] - m) * (x[i + lag] - m) for i in range(n))
    den = sum((v - m) ** 2 for v in x)
    return num / den if den else float("nan")


def vr(r, q):
    """VR(q) = var(suma de q retornos) / (q * var(1 retorno)), solapado."""
    if len(r) < q + 2:
        return None
    v1 = statistics.pvariance(r)
    if v1 == 0:
        return None
    acum = [sum(r[i:i + q]) for i in range(len(r) - q + 1)]
    return statistics.pvariance(acum) / (q * v1)


def tick_pct_por_anio(ts, closes):
    """tick_real/precio_real por año, con el factor de ajuste cancelado."""
    por_anio = defaultdict(list)
    for i in range(1, len(closes)):
        if ts[i][:4] == ts[i - 1][:4]:
            d = abs(closes[i] - closes[i - 1])
            if d > 0:
                por_anio[ts[i][:4]].append(d)
    precio = defaultdict(list)
    for t, c in zip(ts, closes):
        precio[t[:4]].append(c)
    out = {}
    for anio, ds in sorted(por_anio.items()):
        ds.sort()
        # percentil 0.1 en vez del minimo: robusto a un unico dato raro, y con
        # decenas de miles de incrementos por año sigue cayendo en el grano.
        grano = ds[max(0, int(0.001 * len(ds)))]
        out[anio] = 100.0 * grano / (sum(precio[anio]) / len(precio[anio]))
    return out


def fade_ingenua(ts, closes, fills, tick_pct):
    """
    Desvanecer la barra previa: posicion_t = -sign(retorno de la barra t),
    rendimiento realizado con la convencion de ejecucion del proyecto
    (ret_t = pos_t * (fill_{t+1}/fill_t - 1)). Se acumula por año para poder
    enfrentarlo al coste de ESE año.
    """
    bruto = defaultdict(float)
    ops = defaultdict(int)
    pos_prev = 0
    for i in range(1, len(closes) - 1):
        if ts[i][:4] != ts[i - 1][:4]:
            pos_prev = 0
        d = closes[i] - closes[i - 1]
        pos = -1 if d > 0 else (1 if d < 0 else 0)
        if pos != pos_prev:
            ops[ts[i][:4]] += 1
            pos_prev = pos
        if pos:
            bruto[ts[i][:4]] += pos * (fills[i + 1] / fills[i] - 1) * 100.0
    return bruto, ops


def main():
    for etiqueta, max_gap in (("todas las barras", None),
                              ("sin cortes de sesion (fill_gap <= 5 min)", 5)):
        ts, closes, fills, gaps = cargar(max_gap)
        r = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        print(f"\n{'=' * 78}\n{etiqueta}: {len(closes)} barras de 15 min "
              f"({ts[0][:10]} a {ts[-1][:10]})\n{'=' * 78}")

        print("\nAutocorrelacion de los retornos de 15 min")
        se = 1.0 / math.sqrt(len(r))
        for lag in (1, 2, 3, 4, 5, 10, 20):
            a = autocorr(r, lag)
            print(f"  lag {lag:>2}: {a:+.5f}   t = {a / se:+7.1f}")
        print(f"  (error tipico bajo ruido blanco = 1/sqrt(n) = {se:.5f})")

        print("\nRatios de varianza  (<1 reversion, >1 tendencia)")
        for q in (2, 4, 8, 20, 40, 96):
            v = vr(r, q)
            if v is not None:
                print(f"  VR({q:>3}) = {v:.4f}")

        if max_gap is None:
            continue

        tick = tick_pct_por_anio(ts, closes)
        bruto, ops = fade_ingenua(ts, closes, fills, tick)
        print("\nRegla ingenua: desvanecer la barra previa, año a año")
        print(f"  {'año':<6}{'ops':>8}{'bruto %':>10}{'%/op':>10}"
              f"{'coste %':>10}{'x coste':>9}")
        tot_b = tot_o = 0.0
        neto_total = 0.0
        for anio in sorted(bruto):
            b, o, c = bruto[anio], ops[anio], tick[anio]
            por_op = b / o if o else 0.0
            tot_b += b
            tot_o += o
            neto_total += b - o * c
            print(f"  {anio:<6}{o:>8}{b:>10.2f}{por_op:>10.5f}"
                  f"{c:>10.5f}{por_op / c:>9.2f}")
        print(f"  {'TOTAL':<6}{int(tot_o):>8}{tot_b:>10.2f}"
              f"{tot_b / tot_o:>10.5f}{'':>10}")
        print(f"\n  bruto por operacion en toda la muestra : {tot_b / tot_o:+.5f} %")
        print(f"  coste medio ponderado por operacion    : "
              f"{sum(ops[a] * tick[a] for a in ops) / tot_o:.5f} %")
        print(f"  NETO acumulado tras un tick por operacion: {neto_total:+.2f} % de nominal")


if __name__ == "__main__":
    main()
