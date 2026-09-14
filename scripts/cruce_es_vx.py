"""
SP500 x VIX: los cuatro casos, con el VX como indicador (2026-09-14).

Walter: "lo mismo que has hecho antes con las series del ES y E6 intentalo ahora
con ES y VX. VX es el VIX y tiene mucha relacion con ES, por eso tengo
esperanzas de que funcione mejor".

QUE SE OPERA. Solo el ES. El VX entra como indicador y nada mas, porque
operarlo es inviable para una señal de este tamaño: su tick son 0.05 puntos
sobre un indice de ~18 con multiplicador 1000, o sea 50 USD sobre un nominal de
18.000 = 0.28 % de nominal, unas 70 veces el tick del ES (0.004 %). Una señal
de 1-2 puntos basicos no puede pagar eso ni de lejos.

TRES CAUTELAS QUE EL PAR ES x E6 NO NECESITABA

1. EL AJUSTE POR RATIO ES BRUTAL. El VX rueda cada mes y esta en contango, asi
   que el back-ajuste acumulado llega a 122.000x en 2008 (precio ajustado medio
   2.442.831 frente a un VIX real de ~20). Comprobado que la serie SI es
   continua -cero saltos de 1 minuto por encima del 20 %, y los 91 que pasan
   del 5 % caen en aperturas de sesion-, asi que los retornos y por tanto el
   signo y el ER son utilizables. Lo que NO es utilizable es estimar el coste
   del VX por año como hago con ES y E6: el factor cambia doce veces al año.
   Da igual, porque el VX no se opera.

2. EL VX SOLO IMPRIME CUANDO OPERA. 183 barras de 1 minuto al dia en 2008
   frente a 646 en 2025; solo el 25.5 % de las barras de 15 min tienen los 15
   minutos, y el 32.8 % tienen 5 o menos. Un cierre de 15 minutos del VX puede
   ser un precio de hace rato. Por eso se exige un minimo de minutos impresos
   (`MIN_MIN`), y se reporta el resultado con y sin esa exigencia.

3. LA ETIQUETA DE LA BARRA NO COINCIDE. `prepare_bars.py` etiqueta cada barra
   por su ULTIMO minuto impreso: en el ES siempre es :59/:14/:29/:44, pero en
   el VX solo el 61 % de las veces. Juntar las dos series por marca de tiempo
   perderia el 39 % de las barras y, peor, emparejaria mal. Se juntan por
   CUARTO DE HORA (dia, hora, minuto//15), que es el periodo que ambas
   comparten.

Ejecucion y criterio de criba, identicos al estudio ES x E6: señal en el cierre
de 15 min, operacion al minuto siguiente (`fill_price`), horizontes muestreados
sin solapar, y solo se da por superviviente lo que tenga |t| > 2 en LAS DOS
mitades y con el mismo signo.
"""

import csv
from collections import Counter, defaultdict

import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ES = str(DATA_DIR / "ES_pnl_lagged_fast.csv")
VX = str(DATA_DIR / "VX_pnl_lagged_fast.csv")
VX_BARS = str(DATA_DIR / "VX_15min.csv")
VENTANAS = ("n_fast", "n_slow", "n_super")
HORIZONTES = (1, 2, 4, 8, 16, 32)
CASOS = {(-1, -1): "A  ES baja / VIX baja", (+1, +1): "B  ES sube / VIX sube",
         (-1, +1): "C  ES baja / VIX sube", (+1, -1): "D  ES sube / VIX baja"}
MIN_MIN = 8          # minutos impresos exigidos a la barra del VX


def grupo(ts):
    """cuarto de hora al que pertenece la barra: 'YYYY-MM-DD HH Q'."""
    return f"{ts[:10]} {ts[11:13]} {int(ts[14:16]) // 15}"


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


def minutos_vx():
    out = {}
    with open(VX_BARS, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["timestamp"]] = int(row["minutes_in_bar"])
    return out


def rejilla(ts, close):
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(close)):
        if ts[i][:4] == ts[i - 1][:4]:
            d = abs(close[i] - close[i - 1])
            if d > 0:
                inc[ts[i][:4]].append(round(d, 7))
        pre[ts[i][:4]].append(close[i])
    return {y: 100.0 * (sorted(v for v, k in Counter(ds).items()
                               if k >= 0.01 * len(ds)) or [min(ds)])[0]
            / (sum(pre[y]) / len(pre[y])) for y, ds in inc.items()}


def signo_y_er(close, n):
    m = len(close)
    r = np.zeros(m)
    r[1:] = np.log(close[1:] / close[:-1])
    s1 = np.concatenate([[0.0], np.cumsum(r)])
    s2 = np.concatenate([[0.0], np.cumsum(np.abs(r))])
    sg = np.zeros(m, dtype=np.int8)
    er = np.full(m, np.nan)
    t = np.arange(m)
    w = n.astype(int)
    ok = (w >= 2) & (t - w >= 0)
    i0 = np.where(ok, t - w + 1, 0)
    num = np.where(ok, s1[t + 1] - s1[i0], 0.0)
    den = np.where(ok, s2[t + 1] - s2[i0], 0.0)
    b = ok & (den > 0)
    sg[b] = np.sign(num[b]).astype(np.int8)
    er[b] = np.abs(num[b]) / den[b]
    return sg, er


def stats(v):
    if len(v) < 3:
        return 0.0, 0.0
    m = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    return m, (m / (sd / len(v) ** 0.5)) if sd > 0 else 0.0


def main():
    ts_es, cl_es, fi_es, n_es = cargar(ES)
    ts_vx, cl_vx, fi_vx, n_vx = cargar(VX)
    coste_es = rejilla(ts_es, cl_es)
    mins = minutos_vx()

    gvx = {}
    for i, t in enumerate(ts_vx):
        gvx[grupo(t)] = i          # si hay varias, la ultima del cuarto de hora
    par = [(i, gvx[grupo(t)]) for i, t in enumerate(ts_es) if grupo(t) in gvx]
    a = np.array([i for i, _ in par])
    b = np.array([j for _, j in par])
    frescas = np.array([mins.get(ts_vx[j], 0) >= MIN_MIN for j in b])
    anio = np.array([int(ts_es[i][:4]) for i in a])
    c_es = np.array([coste_es[str(y)] for y in anio])

    print(f"ES {len(ts_es)} barras · VX {len(ts_vx)} barras")
    print(f"CUARTOS DE HORA EN COMUN: {len(par)} "
          f"({ts_es[a[0]][:10]} a {ts_es[a[-1]][:10]})")
    print(f"  de ellos con barra de VX de {MIN_MIN}+ minutos impresos: "
          f"{frescas.sum()} ({100 * frescas.mean():.1f}%)")
    porA = defaultdict(lambda: [0, 0])
    for y, f in zip(anio, frescas):
        porA[y][0] += 1
        porA[y][1] += int(f)
    print("  por año (comunes / frescas):  " + " · ".join(
        f"{y}:{v[0]}/{v[1]}" for y, v in sorted(porA.items())))
    print(f"\ncoste de un tick del ES: {c_es.mean():.5f} % de nominal de media")

    supervivientes, pruebas = [], 0
    for filtro, etq_f in ((np.ones(len(par), bool), "TODAS las comunes"),
                          (frescas, f"solo VX con {MIN_MIN}+ minutos")):
        for ven in VENTANAS:
            sg_es, _ = signo_y_er(cl_es, n_es[ven])
            sg_vx, _ = signo_y_er(cl_vx, n_vx[ven])
            s_es, s_vx = sg_es[a], sg_vx[b]
            vale = filtro & (s_es != 0) & (s_vx != 0)
            print(f"\n{'=' * 92}\n{etq_f} · ventana {ven} · {vale.sum()} barras"
                  f"\n{'=' * 92}")
            for cel, nom in CASOS.items():
                k = vale & (s_es == cel[0]) & (s_vx == cel[1])
                print(f"  {nom:<24}{k.sum():>8} ({100 * k.sum() / max(1, vale.sum()):5.1f}%)")
            for h in HORIZONTES:
                print(f"\n  --- horizonte {h} barra{'s' if h > 1 else ''} "
                      f"({h * 15} min), no solapado, rendimiento del ES ---")
                print(f"  {'caso':<24}{'n':>7}{'media %':>11}{'t':>8}"
                      f"{'t 1a':>8}{'t 2a':>8}{'x tick':>9}")
                for cel, nom in CASOS.items():
                    sel = np.where(vale & (s_es == cel[0]) & (s_vx == cel[1]))[0]
                    sel = sel[(sel % h) == 0]
                    sel = sel[a[sel] + h < len(fi_es)]
                    if len(sel) < 50:
                        continue
                    ret = (fi_es[a[sel] + h] / fi_es[a[sel]] - 1) * 100
                    mit = len(ret) // 2
                    m, t = stats(ret)
                    _, t1 = stats(ret[:mit])
                    _, t2 = stats(ret[mit:])
                    cst = c_es[sel].mean()
                    print(f"  {nom:<24}{len(sel):>7}{m:>+11.5f}{t:>+8.2f}"
                          f"{t1:>+8.2f}{t2:>+8.2f}{m / cst:>+8.2f}x")
                    pruebas += 1
                    if abs(t1) > 2 and abs(t2) > 2 and np.sign(t1) == np.sign(t2):
                        supervivientes.append((etq_f, ven, h, nom, m, t, t1, t2,
                                               m / cst, len(sel)))

    print(f"\n\n{'=' * 92}\nSOBREVIVEN AL CORTE (|t|>2 en LAS DOS mitades, mismo signo)"
          f"\n{'=' * 92}")
    print(f"{pruebas} pruebas · por azar se esperarian ~{0.05 * 0.05 * 2 * pruebas:.1f}")
    if not supervivientes:
        print("  NINGUNA.")
    else:
        print(f"  {'muestra':<26}{'ventana':<9}{'h':>3} {'caso':<24}"
              f"{'media %':>10}{'t':>8}{'t 1a':>7}{'t 2a':>7}{'x tick':>9}{'n':>7}")
        for s in sorted(supervivientes, key=lambda x: -abs(x[8])):
            print(f"  {s[0]:<26}{s[1]:<9}{s[2]:>3} {s[3]:<24}"
                  f"{s[4]:>+10.5f}{s[5]:>+8.2f}{s[6]:>+7.2f}{s[7]:>+7.2f}"
                  f"{s[8]:>+8.2f}x{s[9]:>7}")


if __name__ == "__main__":
    main()
