"""
Las tres ideas, metidas en un LOTO que elige solo.

Walter aprobo probar tres cosas sobre los cuatro casos ES x E6:

  1. ER CON SIGNO: los cuatro casos son el SIGNO de la misma variable cuyo
     MODULO es el ratio de Kaufman ya validado. Asi que se cruza el caso con
     el filtro de |ER| en la cola baja (donde la reversion paga en el E6).
  2. N COMO REGIMEN, no como ventana: el cociente N_fast/N_super es endogeno,
     adimensional, y mide persistencia de rachas de forma distinta al ER -el
     ER mide recorrido neto, el cociente mide cuanto duran las rachas-.
  3. ACUERDO DE N ENTRE ACTIVOS: operar solo cuando los dos mercados estan a
     la vez en regimen de rachas largas (o los dos en el contrario).

HONESTIDAD SOBRE EL ORIGEN. El caso C se encontro mirando la muestra entera,
asi que un LOTO que diera por bueno "C" y solo optimizase el filtro estaria
validando la parte facil. Por eso aqui el LOTO elige TAMBIEN el caso, el
instrumento, la direccion y la ventana: si vuelve a caer en C/ES/largo por su
cuenta en la mayoria de los pliegues, eso ya es evidencia; si no, el hallazgo
anterior era fragil. Lo unico que sigue viniendo de fuera es el conjunto de
candidatos, que lo elegi yo.

La posicion se mantiene mientras se cumplen caso y filtro, asi que el horizonte
es endogeno y no añade ningun parametro. Coste: medio tick por unidad de cambio
de posicion, con el tick REAL de cada año (el del ES ha caido 5x en la muestra).
Ejecucion fill-a-fill: señal en el cierre de 15 min, operacion al minuto
siguiente.
"""

import csv
from collections import Counter, defaultdict

import numpy as np

ES = "/home/claude/es/ES_pnl_lagged_fast.csv"
E6 = "/mnt/user-data/uploads/trading_nautilus/data/E6_pnl_lagged_fast.csv"
VENTANAS = ("n_fast", "n_slow", "n_super")
CASOS = {"A": (-1, -1), "B": (+1, +1), "C": (-1, +1), "D": (+1, -1)}
CUTS = (0.10, 0.20, 0.30, 0.50)
FILTROS = ("sin_filtro", "er_bajo", "er_alto", "ratio_bajo", "ratio_alto",
           "acuerdoN_alto", "acuerdoN_bajo")
N_FOLDS = 10
MIN_OPS = 100


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


def signo_y_er(close, n):
    """signo del incremento sobre la ventana endogena, y |ER| sobre la misma."""
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
    buena = ok & (den > 0)
    sg[buena] = np.sign(num[buena]).astype(np.int8)
    er[buena] = np.abs(num[buena]) / den[buena]
    return sg, er


def main():
    ts_es, cl_es, fi_es, n_es = cargar(ES)
    ts_e6, cl_e6, fi_e6, n_e6 = cargar(E6)
    c_es_y, c_e6_y = rejilla(ts_es, cl_es), rejilla(ts_e6, cl_e6)

    ix = {t: i for i, t in enumerate(ts_e6)}
    par = [(i, ix[t]) for i, t in enumerate(ts_es) if t in ix]
    a = np.array([i for i, _ in par])
    b = np.array([j for _, j in par])
    T = len(a)
    anio = np.array([int(ts_es[i][:4]) for i in a])
    print(f"{T} barras comunes, {ts_es[a[0]][:10]} a {ts_es[a[-1]][:10]}")

    ret, coste = {}, {}
    for etq, fi, ii, cy in (("ES", fi_es, a, c_es_y), ("E6", fi_e6, b, c_e6_y)):
        v = np.zeros(T)
        v[:-1] = (fi[ii[1:]] / fi[ii[:-1]] - 1) * 100
        ret[etq] = v
        coste[etq] = np.array([cy[str(y)] for y in anio])

    sg, er = {}, {}
    for etq, cl, ns, ii in (("ES", cl_es, n_es, a), ("E6", cl_e6, n_e6, b)):
        for w in VENTANAS:
            s, e = signo_y_er(cl, ns[w])
            sg[(etq, w)] = s[ii]
            er[(etq, w)] = e[ii]

    # idea 2: N_fast / N_super, adimensional
    ratio = {}
    for etq, ns, ii in (("ES", n_es, a), ("E6", n_e6, b)):
        f = ns["n_fast"][ii].astype(float)
        s = ns["n_super"][ii].astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio[etq] = np.where(s > 0, f / s, np.nan)
    for etq in ("ES", "E6"):
        r = ratio[etq][np.isfinite(ratio[etq])]
        print(f"  N_fast/N_super {etq}: mediana {np.median(r):.4f}, "
              f"p10 {np.quantile(r, .1):.4f}, p90 {np.quantile(r, .9):.4f}")

    bordes = [T * i // N_FOLDS for i in range(N_FOLDS + 1)]

    def mascara(etq, w, filtro, cut_q, entren):
        """El corte se fija SIEMPRE con el cuantil del conjunto de entrenamiento."""
        if filtro == "sin_filtro":
            return np.ones(T, dtype=bool)
        if filtro in ("er_bajo", "er_alto"):
            v = er[(etq, w)]
        elif filtro in ("ratio_bajo", "ratio_alto"):
            v = ratio[etq]
        else:
            v = None
        if v is not None:
            disp = v[entren & np.isfinite(v)]
            if len(disp) < 100:
                return None
            c = float(np.quantile(disp, cut_q))
            m = np.isfinite(v) & ((v < c) if filtro.endswith("bajo") else (v > c))
            return m
        # acuerdo de N: los dos mercados a la vez
        ms = []
        for e2 in ("ES", "E6"):
            v2 = ratio[e2]
            disp = v2[entren & np.isfinite(v2)]
            if len(disp) < 100:
                return None
            c = float(np.quantile(disp, cut_q))
            ms.append(np.isfinite(v2) & ((v2 > c) if filtro.endswith("alto") else (v2 < c)))
        return ms[0] & ms[1]

    def series(etq, caso, w, filtro, cut_q, entren):
        m = mascara(etq, w, filtro, cut_q, entren)
        if m is None:
            return None
        ce, c6 = CASOS[caso]
        dentro = m & (sg[("ES", w)] == ce) & (sg[("E6", w)] == c6)
        pos = dentro.astype(float)
        dp = np.abs(np.diff(np.concatenate([[0.0], pos])))
        pnl = pos * ret[etq]
        cst = dp * coste[etq] / 2
        ent = dp > 0
        return pnl, cst, ent

    combos = [(etq, caso, w, filtro, cut)
              for etq in ("ES", "E6") for caso in CASOS for w in VENTANAS
              for filtro in FILTROS
              for cut in ((0.0,) if filtro == "sin_filtro" else CUTS)]
    print(f"\n{len(combos)} combinaciones candidatas x 2 direcciones "
          f"x {N_FOLDS} pliegues\n")

    # Para cada pliegue se recalcula el corte con SU 90%, asi que no se puede
    # cachear entre pliegues: se recorre pliegue a pliegue.
    res_fold = {"neto_total": [], "neto_por_op": []}
    for f in range(N_FOLDS):
        test = np.zeros(T, dtype=bool)
        test[bordes[f]:bordes[f + 1]] = True
        entren = ~test
        mejor = {"neto_total": None, "neto_por_op": None}
        for (etq, caso, w, filtro, cut) in combos:
            s = series(etq, caso, w, filtro, cut, entren)
            if s is None:
                continue
            pnl, cst, ent = s
            for dnom, sgn in (("largo", 1.0), ("corto", -1.0)):
                ops_in = int(ent[entren].sum())
                if ops_in < MIN_OPS:
                    continue
                neto_in = float((sgn * pnl - cst)[entren].sum())
                fila = (neto_in, etq, caso, w, filtro, cut, dnom, sgn, ops_in,
                        float((sgn * pnl - cst)[test].sum()), int(ent[test].sum()),
                        float((sgn * pnl)[test].sum()), float(cst[test].sum()))
                for obj, score in (("neto_total", neto_in),
                                   ("neto_por_op", neto_in / ops_in)):
                    if mejor[obj] is None or score > mejor[obj][0]:
                        mejor[obj] = (score,) + fila[1:]
        for obj in res_fold:
            res_fold[obj].append(mejor[obj])

    for obj in ("neto_total", "neto_por_op"):
        print(f"\n{'=' * 92}\nOBJETIVO DENTRO DE MUESTRA = {obj}\n{'=' * 92}")
        for f, mj in enumerate(res_fold[obj]):
            if mj:
                print(f"  pliegue {f + 1:>2}: {mj[1]} caso {mj[2]} {mj[3]} "
                      f"{mj[4]}({mj[5]:.2f}) {mj[6]:<6} | {mj[8]} ops dentro"
                      f" | FUERA {mj[9]:>7.2f} ({mj[10]} ops)")
        resumen(res_fold[obj], anio)


def resumen(res_fold, anio):
    val = [r for r in res_fold if r]
    tot = sum(r[9] for r in val)
    ops = sum(r[10] for r in val)
    bru = sum(r[11] for r in val)
    cos = sum(r[12] for r in val)
    anios = len(set(anio))
    print(f"LOTO: bruto {bru:+.2f} % · coste {cos:.2f} % · NETO {tot:+.2f} % "
          f"en {anios} años = {tot / anios:+.3f} %/año")
    # OJO: `ops` cuenta cada cambio de posicion, o sea entradas Y salidas, asi
    # que una ida y vuelta son 2. El coste de una ida y vuelta es un tick entero
    # (medio por cada cruce), y es contra eso contra lo que hay que comparar.
    vueltas = ops // 2
    print(f"      {vueltas} idas y vueltas · "
          f"{tot / vueltas if vueltas else 0:+.5f} % neto por ida y vuelta · "
          f"bruto {bru / vueltas if vueltas else 0:.5f} % · "
          f"{sum(1 for r in val if r[9] > 0)}/{len(val)} pliegues positivos")
    c = Counter((r[1], r[2], r[3], r[4], r[6]) for r in val)
    print("\nQue elige el LOTO cuando decide solo:")
    for k, v in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {v:>2}/10   {k[0]} · caso {k[1]} · {k[2]} · {k[3]} · {k[4]}")


if __name__ == "__main__":
    main()
