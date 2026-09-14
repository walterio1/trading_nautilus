"""
SP500 x EUR/USD: los cuatro casos de Walter (2026-09-13).

    "caso A: sp500 debil en los ultimos N; E6 debil en los ultimos N;
     caso B: en vez de debil y debil, fuerte y fuerte;
     casos C y D: debil y fuerte; fuerte y debil"

y su indicacion de que las N endogenas "se pueden utilizar para hallar
incrementos, no necesariamente medias moviles".

EL INCREMENTO SOBRE N, NO LA MEDIA. Para cada instrumento y cada barra:

    inc_X(t) = close_X(t) - close_X(t - N_X(t))

con N_X la ventana endogena DEL PROPIO instrumento (n_fast, n_slow o n_super,
las tres se prueban). fuerte = inc > 0, debil = inc < 0. Solo usa cierres hasta
t: es causal.

De regalo sale una variable que el proyecto no estaba usando: el ER CON SIGNO,

    ER_con_signo = inc_X(t) / suma de |incrementos de barra| sobre esa ventana

cuyo VALOR ABSOLUTO es el ratio de eficiencia de Kaufman -el filtro de regimen
que ya esta validado- y cuyo SIGNO es justo el fuerte/debil de arriba. O sea
que los cuatro casos y el filtro que funciona son la misma variable mirada de
dos maneras, y se pueden cruzar.

EJECUCION, como recordo Walter: la señal se lee en el cierre de 15 minutos
(:59, :14, :29, :44) y se opera al cierre del primer minuto posterior. El
rendimiento a h barras es por tanto fill-a-fill,

    ret_X(t, h) = fill_X(t+h) / fill_X(t) - 1

de modo que tanto la entrada como la salida pagan ese minuto de retraso.

DOS CAUTELAS DE MEDIDA, metidas a proposito:

  * MUESTRAS NO SOLAPADAS. Con h > 1, tomar una observacion por barra solapa
    h-1 barras entre observaciones consecutivas e infla el estadistico t por
    un factor de hasta sqrt(h). Aqui cada horizonte se muestrea cada h barras.
  * MULTIPLICIDAD. 3 ventanas x 6 horizontes x 4 casos x 2 instrumentos son
    144 pruebas: al 5 % saldrian ~7 "significativas" por puro azar. Por eso el
    corte no es el t de la muestra completa sino que las DOS mitades tengan el
    mismo signo y |t| > 2 cada una, y se compara cuantas sobreviven con
    cuantas se esperarian por azar.
"""

import csv
from collections import Counter, defaultdict

import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ES = str(DATA_DIR / "ES_pnl_lagged_fast.csv")
E6 = str(DATA_DIR / "E6_pnl_lagged_fast.csv")
VENTANAS = ("n_fast", "n_slow", "n_super")
HORIZONTES = (1, 2, 4, 8, 16, 32)
CASOS = {(-1, -1): "A  debil/debil", (+1, +1): "B  fuerte/fuerte",
         (-1, +1): "C  ES debil/E6 fuerte", (+1, -1): "D  ES fuerte/E6 debil"}


def cargar(path):
    ts, close, fill = [], [], []
    ns = {k: [] for k in VENTANAS}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts.append(row["timestamp"])
            close.append(float(row["close"]))
            fill.append(float(row["fill_price"]))
            for k in VENTANAS:
                ns[k].append(int(row[k] or 0))
    return ts, np.array(close), np.array(fill), {k: np.array(v) for k, v in ns.items()}


def rejilla(ts, close):
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(close)):
        if ts[i][:4] == ts[i - 1][:4]:
            d = abs(close[i] - close[i - 1])
            if d > 0:
                inc[ts[i][:4]].append(round(d, 7))
        pre[ts[i][:4]].append(close[i])
    out = {}
    for y, ds in inc.items():
        c = Counter(ds)
        cand = sorted(v for v, k in c.items() if k >= 0.01 * len(ds))
        g = cand[0] if cand else min(ds)
        out[y] = 100.0 * g / (sum(pre[y]) / len(pre[y]))
    return out


def señal(close, n):
    """signo e |ER| del incremento sobre la ventana endogena, barra a barra."""
    m = len(close)
    r = np.zeros(m)
    r[1:] = np.log(close[1:] / close[:-1])
    s1 = np.concatenate([[0.0], np.cumsum(r)])
    s2 = np.concatenate([[0.0], np.cumsum(np.abs(r))])
    sg = np.zeros(m, dtype=int)
    er = np.full(m, np.nan)
    for t in range(m):
        w = n[t]
        if w < 2 or t - w < 0:
            continue
        num = s1[t + 1] - s1[t - w + 1]
        den = s2[t + 1] - s2[t - w + 1]
        if den <= 0:
            continue
        sg[t] = 1 if num > 0 else (-1 if num < 0 else 0)
        er[t] = abs(num) / den
    return sg, er


def stats(v):
    if len(v) < 3:
        return 0.0, 0.0
    m = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    return m, (m / (sd / len(v) ** 0.5)) if sd > 0 else 0.0


def main():
    ts_es, cl_es, fi_es, n_es = cargar(ES)
    ts_e6, cl_e6, fi_e6, n_e6 = cargar(E6)
    coste_es, coste_e6 = rejilla(ts_es, cl_es), rejilla(ts_e6, cl_e6)

    idx_e6 = {t: i for i, t in enumerate(ts_e6)}
    comun = [(i, idx_e6[t]) for i, t in enumerate(ts_es) if t in idx_e6]
    a = np.array([i for i, _ in comun])
    b = np.array([j for _, j in comun])
    print(f"ES {len(ts_es)} barras · E6 {len(ts_e6)} barras · "
          f"EN COMUN {len(comun)} ({ts_es[a[0]][:10]} a {ts_es[a[-1]][:10]})")
    print(f"solapan el {100 * len(comun) / len(ts_es):.1f}% del ES y "
          f"el {100 * len(comun) / len(ts_e6):.1f}% del E6")

    anios = np.array([int(ts_es[i][:4]) for i in a])
    c_es = np.array([coste_es[str(y)] for y in anios])
    c_e6 = np.array([coste_e6[str(y)] for y in anios])
    print(f"\ncoste medio por operacion: ES {c_es.mean():.5f} % · "
          f"E6 {c_e6.mean():.5f} % de nominal")

    supervivientes = []
    total_pruebas = 0

    for ven in VENTANAS:
        sg_es, er_es = señal(cl_es, n_es[ven])
        sg_e6, er_e6 = señal(cl_e6, n_e6[ven])
        s_es, s_e6 = sg_es[a], sg_e6[b]
        vale = (s_es != 0) & (s_e6 != 0)

        print(f"\n{'=' * 104}\nVENTANA {ven} — reparto de los cuatro casos"
              f"\n{'=' * 104}")
        for cel, nom in CASOS.items():
            k = vale & (s_es == cel[0]) & (s_e6 == cel[1])
            print(f"  {nom:<24}{k.sum():>8} barras ({100 * k.sum() / vale.sum():5.1f}%)")

        for h in HORIZONTES:
            print(f"\n  --- horizonte {h} barra{'s' if h > 1 else ''} "
                  f"({h * 15} min), muestreo no solapado ---")
            print(f"  {'caso':<24}{'n':>6}"
                  f"{'ES media%':>11}{'t':>7}{'t 1a':>7}{'t 2a':>7}{'x coste':>9}"
                  f"{'E6 media%':>11}{'t':>7}{'t 1a':>7}{'t 2a':>7}{'x coste':>9}")
            for cel, nom in CASOS.items():
                sel = np.where(vale & (s_es == cel[0]) & (s_e6 == cel[1]))[0]
                sel = sel[(sel % h) == 0]                       # no solapadas
                sel = sel[a[sel] + h < len(fi_es)]
                sel = sel[b[sel] + h < len(fi_e6)]
                if len(sel) < 50:
                    continue
                fila = f"  {nom:<24}{len(sel):>6}"
                for etq, fi, ii, coste in (("ES", fi_es, a, c_es), ("E6", fi_e6, b, c_e6)):
                    ret = (fi[ii[sel] + h] / fi[ii[sel]] - 1) * 100
                    mitad = len(ret) // 2
                    m, t = stats(ret)
                    _, t1 = stats(ret[:mitad])
                    _, t2 = stats(ret[mitad:])
                    cst = coste[sel].mean()
                    fila += f"{m:>+11.5f}{t:>+7.2f}{t1:>+7.2f}{t2:>+7.2f}{m / cst:>+8.2f}x"
                    total_pruebas += 1
                    if abs(t1) > 2 and abs(t2) > 2 and np.sign(t1) == np.sign(t2):
                        supervivientes.append((ven, h, nom, etq, m, t, t1, t2, m / cst, len(sel)))
                print(fila)

    print(f"\n\n{'=' * 104}\nLO QUE SOBREVIVE AL CORTE HONESTO "
          f"(|t| > 2 en LAS DOS mitades y con el mismo signo)\n{'=' * 104}")
    esperados = 0.05 * 0.05 * 2 * total_pruebas    # ~p<0.05 en ambas mitades, mismo signo
    print(f"{total_pruebas} pruebas · por azar se esperarian ~{esperados:.1f}")
    if not supervivientes:
        print("  NINGUNA.")
    else:
        print(f"  {'ventana':<10}{'h':>4}{'caso':<24}{'inst':>5}"
              f"{'media%':>10}{'t':>7}{'t 1a':>7}{'t 2a':>7}{'x coste':>9}{'n':>7}")
        for s in sorted(supervivientes, key=lambda x: -abs(x[8])):
            print(f"  {s[0]:<10}{s[1]:>4}{s[2]:<24}{s[3]:>5}"
                  f"{s[4]:>+10.5f}{s[5]:>+7.2f}{s[6]:>+7.2f}{s[7]:>+7.2f}"
                  f"{s[8]:>+8.2f}x{s[9]:>7}")


if __name__ == "__main__":
    main()
