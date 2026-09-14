"""
Los cuatro casos, convertidos en operativa y costeados.

La tabla de `cruce_es_e6.py` mide rendimiento esperado a h barras, que no es
dinero: no dice cuantas veces hay que pagar el spread. Aqui la posicion se
MANTIENE MIENTRAS EL CASO PERSISTE y se cierra cuando cambia, que es la forma
de menor rotacion de expresar la misma idea:

    posicion_X(t) = señal del caso  si  (signo_ES, signo_E6) == caso
                    0               en otro caso

Una operacion = cada entrada. El coste se carga a media rejilla por unidad de
cambio de posicion (|Δpos| x medio tick), igual que en `deadband_test.py`: asi
entrar y salir a plano cuesta un tick entre las dos, y dar la vuelta cuesta uno
entero de golpe.

Rendimiento fill-a-fill, con la señal leida en el cierre de 15 minutos y la
ejecucion al minuto siguiente (`fill_price`), que es la convencion del proyecto.
"""

import csv
from collections import Counter, defaultdict

import numpy as np

ES = "/home/claude/es/ES_pnl_lagged_fast.csv"
E6 = "/mnt/user-data/uploads/trading_nautilus/data/E6_pnl_lagged_fast.csv"
VENTANAS = ("n_fast", "n_slow", "n_super")
CASOS = {(-1, -1): "A debil/debil", (+1, +1): "B fuerte/fuerte",
         (-1, +1): "C ESdeb/E6fue", (+1, -1): "D ESfue/E6deb"}


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
    return {y: 100.0 * (sorted(v for v, k in Counter(ds).items()
                               if k >= 0.01 * len(ds)) or [min(ds)])[0]
            / (sum(pre[y]) / len(pre[y])) for y, ds in inc.items()}


def signo(close, n):
    m = len(close)
    r = np.zeros(m)
    r[1:] = np.log(close[1:] / close[:-1])
    s1 = np.concatenate([[0.0], np.cumsum(r)])
    sg = np.zeros(m, dtype=int)
    for t in range(m):
        w = n[t]
        if w >= 2 and t - w >= 0:
            d = s1[t + 1] - s1[t - w + 1]
            sg[t] = 1 if d > 0 else (-1 if d < 0 else 0)
    return sg


def main():
    ts_es, cl_es, fi_es, n_es = cargar(ES)
    ts_e6, cl_e6, fi_e6, n_e6 = cargar(E6)
    c_es_y, c_e6_y = rejilla(ts_es, cl_es), rejilla(ts_e6, cl_e6)

    ix = {t: i for i, t in enumerate(ts_e6)}
    par = [(i, ix[t]) for i, t in enumerate(ts_es) if t in ix]
    a = np.array([i for i, _ in par])
    b = np.array([j for _, j in par])
    anio = np.array([int(ts_es[i][:4]) for i in a])
    T = len(a)

    # rendimiento fill-a-fill de cada barra comun, para cada instrumento
    ret = {}
    for etq, fi, ii in (("ES", fi_es, a), ("E6", fi_e6, b)):
        v = np.zeros(T)
        v[:-1] = (fi[ii[1:]] / fi[ii[:-1]] - 1) * 100
        ret[etq] = v
    coste = {"ES": np.array([c_es_y[str(y)] for y in anio]),
             "E6": np.array([c_e6_y[str(y)] for y in anio])}

    print(f"{T} barras comunes, {ts_es[a[0]][:10]} a {ts_es[a[-1]][:10]}\n")
    h = (f"{'ventana':<9}{'caso':<16}{'inst':>4}{'dir':>6}{'ops':>7}"
         f"{'bruto%':>10}{'coste%':>9}{'NETO%':>10}{'%/op':>10}{'x cost':>8}"
         f"{'neto 1a':>9}{'neto 2a':>9}{'años+':>7}")
    print(h)
    print("-" * len(h))
    filas = []
    for ven in VENTANAS:
        s_es = signo(cl_es, n_es[ven])[a]
        s_e6 = signo(cl_e6, n_e6[ven])[b]
        vale = (s_es != 0) & (s_e6 != 0)
        for cel, nom in CASOS.items():
            dentro = vale & (s_es == cel[0]) & (s_e6 == cel[1])
            for etq in ("ES", "E6"):
                for dnom, sg in (("largo", +1.0), ("corto", -1.0)):
                    pos = np.where(dentro, sg, 0.0)
                    dpos = np.abs(np.diff(np.concatenate([[0.0], pos, [0.0]])))
                    # medio tick por unidad de cambio de posicion
                    cst_bar = np.zeros(T)
                    cst_bar += dpos[:-1] * coste[etq] / 2
                    cst_bar[:-1] += dpos[1:-1] * coste[etq][:-1] / 2 * 0
                    cst = float((dpos[:-1] * coste[etq] / 2).sum()
                                + dpos[-1] * coste[etq][-1] / 2)
                    entradas = int(((pos != 0) & (np.concatenate([[0.0], pos[:-1]]) == 0)).sum())
                    pnl_bar = pos * ret[etq]
                    bruto = float(pnl_bar.sum())
                    neto = bruto - cst
                    if entradas < 50:
                        continue
                    mitad = T // 2
                    n1 = float(pnl_bar[:mitad].sum()) - float(
                        (np.abs(np.diff(np.concatenate([[0.0], pos[:mitad], [0.0]])))[:-1]
                         * coste[etq][:mitad] / 2).sum())
                    n2 = neto - n1
                    por_anio = defaultdict(float)
                    for y, x in zip(anio, pnl_bar):
                        por_anio[y] += x
                    anios_pos = sum(1 for v in por_anio.values() if v > 0)
                    filas.append((abs(neto), f"{ven:<9}{nom:<16}{etq:>4}{dnom:>6}{entradas:>7}"
                                  f"{bruto:>10.2f}{cst:>9.2f}{neto:>10.2f}"
                                  f"{neto / entradas:>10.5f}"
                                  f"{bruto / entradas / coste[etq].mean():>7.2f}x"
                                  f"{n1:>9.2f}{n2:>9.2f}{anios_pos:>5}/18", neto, n1, n2))
    for _, linea, neto, n1, n2 in sorted(filas, key=lambda x: -x[2]):
        print(linea)

    print("\n  'x cost' = bruto por operacion dividido por un tick. Por debajo de 1 la idea")
    print("  no paga el spread por mucho que el estadistico acompañe.")
    print("  'neto 1a'/'neto 2a' parten la muestra por la mitad: una idea real gana en las dos.")


if __name__ == "__main__":
    main()
