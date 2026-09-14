"""
LOTO del E6, version 2 — con el coste bien medido y tres funciones objetivo.

DOS CORRECCIONES sobre la primera pasada:

1. EL COSTE. El memorandum usa 0.00455 % fijo, "un tick del E6 con el euro a
   1.10". Medida la rejilla real de la serie -los incrementos caen sobre un
   grano exacto, y el factor del ajuste por ratio se cancela al dividir grano
   entre precio-, resulta que:

       2008-2015   grano 0.00012   ->  coste 0.0068 % - 0.0090 % de nominal
       2016        grano 0.00006   ->  transicion
       2017-2025   grano 0.00005   ->  coste 0.0037 % - 0.0045 %

   O sea que el tick del E6 se PARTIO POR LA MITAD en 2016, y durante los ocho
   primeros años de la muestra costaba entre un 50 % y un 97 % mas de lo que
   supone el memorandum. Justo los años que mas beneficio aportan (2008 solo,
   +14.60 % segun el memorandum).

2. LA FUNCION OBJETIVO. Maximizar el NETO TOTAL dentro de muestra elige cortes
   demasiado flojos: mas operaciones suben el total aunque cada una aporte
   menos, y las marginales son las que no sobreviven fuera. Se prueban tres
   objetivos, y se dice cual se usa, porque la eleccion del objetivo es en si
   misma un grado de libertad que el LOTO no valida:

       neto_total    suma del neto en el 90%
       neto_por_op   neto medio por operacion
       t_neto        t del neto por tramo (equilibra tamaño y consistencia)
"""

import csv
from collections import Counter, defaultdict

import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PNL = str(DATA_DIR / "E6_pnl_lagged_fast.csv")
PAIRINGS = ("a", "b", "c")
COST_FIJO = 0.00455
N_FOLDS = 10
CUANTILES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 1.00]
VENTANAS = ["tramo_prev", "n_fast", "n_slow", "n_super", "2n_super",
            "dia_96", "semana_480", "mes_1920"]
DIRECCIONES = {"reversion": -1.0, "tendencia": +1.0}


def cargar():
    ts, close, nf, ns, nsu, seg = [], [], [], [], [], []
    pos = {k: [] for k in PAIRINGS}
    ret = {k: [] for k in PAIRINGS}
    with open(PNL, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts.append(row["timestamp"])
            close.append(float(row["close"]))
            nf.append(int(row["n_fast"] or 0))
            ns.append(int(row["n_slow"] or 0))
            nsu.append(int(row["n_super"] or 0))
            seg.append(int(row["seg_id"]))
            for k in PAIRINGS:
                pos[k].append(int(row[f"pos_{k}"] or 0))
                ret[k].append(float(row[f"ret_{k}"]))
    return (ts, np.array(close), np.array(nf), np.array(ns), np.array(nsu),
            np.array(seg), {k: np.array(v) for k, v in pos.items()},
            {k: np.array(v) for k, v in ret.items()})


def rejilla_por_anio(ts, close):
    """
    Coste de cruzar un tick, en % de nominal, año a año.

    El grano de la rejilla se estima como el MENOR incremento que se repite en
    al menos el 1 % de las barras: los incrementos caen sobre multiplos exactos
    del tick, asi que ese valor es el tick en las unidades (ajustadas) de la
    serie. Dividirlo por el precio medio (tambien ajustado) cancela el factor
    del ajuste por ratio y deja tick_real / precio_real, que es el coste que
    de verdad se pagaba entonces.
    """
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(close)):
        if ts[i][:4] == ts[i - 1][:4]:
            d = abs(close[i] - close[i - 1])
            if d > 0:
                inc[ts[i][:4]].append(round(d, 7))
        pre[ts[i][:4]].append(close[i])
    out, grano = {}, {}
    for y, ds in inc.items():
        c = Counter(ds)
        cand = sorted(v for v, k in c.items() if k >= 0.01 * len(ds))
        g = cand[0] if cand else min(ds)
        p = sum(pre[y]) / len(pre[y])
        grano[y] = g
        out[y] = 100.0 * g / p
    return out, grano


def main():
    ts, close, nf, ns, nsu, seg, pos, ret = cargar()
    n = len(close)
    coste_anio, grano = rejilla_por_anio(ts, close)

    print(f"{n} barras de 15 min, {ts[0][:10]} a {ts[-1][:10]}\n")
    print("COSTE REAL DE UN TICK, medido de la rejilla de la propia serie")
    print(f"  {'año':<6}{'grano':>11}{'coste %':>10}{'vs 0.00455':>12}")
    for y in sorted(coste_anio):
        print(f"  {y:<6}{grano[y]:>11.5f}{coste_anio[y]:>10.5f}"
              f"{coste_anio[y] / COST_FIJO:>11.2f}x")

    r = np.zeros(n)
    r[1:] = np.log(close[1:] / close[:-1])
    s1 = np.concatenate([[0.0], np.cumsum(r)])
    s2 = np.concatenate([[0.0], np.cumsum(np.abs(r))])

    def er(a, b):
        """ER sobre los retornos a..b inclusive, o sea sobre los cierres a-1..b."""
        if b < a or a < 1:
            return np.nan
        den = s2[b + 1] - s2[a]
        return abs(s1[b + 1] - s1[a]) / den if den > 0 else np.nan

    ini, fin = {}, {}
    for i, sid in enumerate(seg):
        if sid not in ini:
            ini[sid] = i
        fin[sid] = i
    sids = sorted(ini)[1:-1]
    m = len(sids)

    pct = {k: np.zeros(m) for k in PAIRINGS}
    ops = {k: np.zeros(m, dtype=int) for k in PAIRINGS}
    for k in PAIRINGS:
        p = pos[k]
        cam = np.zeros(n, dtype=bool)
        cam[1:] = (p[1:] != p[:-1]) & (p[1:] != 0)
        cam[0] = p[0] != 0
        co = np.concatenate([[0], np.cumsum(cam)])
        cr = np.concatenate([[0.0], np.cumsum(ret[k])])
        for j, sid in enumerate(sids):
            a, b = ini[sid], fin[sid]
            pct[k][j] = cr[b + 1] - cr[a]
            ops[k][j] = co[b + 1] - co[a]

    anio = np.array([int(ts[ini[sid]][:4]) for sid in sids])
    coste_var = np.array([coste_anio[str(a)] for a in anio])
    coste_fix = np.full(m, COST_FIJO)

    MIN_BARS = 8
    ER = {}
    for v in VENTANAS:
        vals = np.full(m, np.nan)
        for j, sid in enumerate(sids):
            b = ini[sid] - 1
            if b < 1:
                continue
            prev_sid = seg[b]
            if fin[prev_sid] - ini[prev_sid] + 1 < MIN_BARS:
                continue
            if v == "tramo_prev":
                # Los cierres DEL tramo anterior: el primer retorno que cuenta
                # es el que va de su primer cierre al segundo, no el que entra
                # en el tramo desde la barra de antes (esa es de otro tramo).
                # Sin este +1 la ventana lleva un retorno de mas y el ER sale
                # distinto del de regime_variance.py.
                a = ini[seg[b]] + 1
            else:
                w = {"n_fast": nf[b], "n_slow": ns[b], "n_super": nsu[b],
                     "2n_super": 2 * nsu[b], "dia_96": 96,
                     "semana_480": 480, "mes_1920": 1920}[v]
                if w < 3:
                    continue
                a = b - w + 2          # w CIERRES -> w-1 retornos, acabando en b
            if a < 1:
                continue
            vals[j] = er(a, b)
        ER[v] = vals

    def neto_por_tramo(mask, pairing, ventana, corte, signo, coste):
        sel = mask & np.isfinite(ER[ventana]) & (ER[ventana] < corte)
        if not sel.any():
            return None
        o = ops[pairing][sel]
        return signo * pct[pairing][sel] - o * coste[sel], o

    def puntuar(mask, pairing, ventana, corte, signo, coste, objetivo):
        res = neto_por_tramo(mask, pairing, ventana, corte, signo, coste)
        if res is None:
            return None
        netos, o = res
        tot_ops = int(o.sum())
        if tot_ops < 30:
            return None
        total = float(netos.sum())
        if objetivo == "neto_total":
            score = total
        elif objetivo == "neto_por_op":
            score = total / tot_ops
        else:  # t_neto
            sd = netos.std(ddof=1) if len(netos) > 1 else 0.0
            score = (netos.mean() / (sd / len(netos) ** 0.5)) if sd > 0 else 0.0
        return score, total, tot_ops

    bordes = [m * i // N_FOLDS for i in range(N_FOLDS + 1)]
    idx = np.arange(m)

    def mejor_en(mask, coste, objetivo):
        mejor = None
        for pairing in PAIRINGS:
            for ventana in VENTANAS:
                v = ER[ventana]
                disp = v[mask & np.isfinite(v)]
                if len(disp) < 50:
                    continue
                for q in CUANTILES:
                    corte = np.inf if q >= 1.0 else float(np.quantile(disp, q))
                    for dname, signo in DIRECCIONES.items():
                        got = puntuar(mask, pairing, ventana, corte, signo, coste, objetivo)
                        if got is None:
                            continue
                        if mejor is None or got[0] > mejor[0]:
                            mejor = (got[0], got[1], got[2], pairing, ventana, q, corte, dname, signo)
        return mejor

    anios_tot = int(ts[-1][:4]) - int(ts[0][:4]) + 1

    for cnombre, coste in (("coste real por año", coste_var),
                           ("coste fijo 0.00455 (memorandum)", coste_fix)):
        print(f"\n\n{'#' * 96}\n##  {cnombre}\n{'#' * 96}")

        # Referencia: la configuracion publicada, sin optimizar nada.
        sel = np.isfinite(ER["tramo_prev"]) & (ER["tramo_prev"] < 0.055)
        bruto = -pct["c"][sel].sum()
        cst = (ops["c"][sel] * coste[sel]).sum()
        print(f"\nConfiguracion del memorandum (C, tramo previo, corte 0.055, reversion),")
        print(f"muestra completa, corte elegido mirandola entera:")
        print(f"  bruto {bruto:+.2f} %   coste {cst:.2f} %   NETO {bruto - cst:+.2f} %"
              f"   ({int(ops['c'][sel].sum())} ops)   {(bruto - cst) / anios_tot:+.3f} %/año")

        for objetivo in ("neto_total", "neto_por_op", "t_neto"):
            print(f"\n{'=' * 96}\nLOTO, objetivo dentro de muestra = {objetivo}\n{'=' * 96}")
            h = (f"{'pliegue':<8}{'elegido con el 90%':>36}{'dentro':>10}"
                 f"{'ops':>7}{'FUERA':>10}{'ops':>7}")
            print(h)
            print("-" * len(h))
            tot, tops, elig, netos = 0.0, 0, [], []
            for f in range(N_FOLDS):
                test = (idx >= bordes[f]) & (idx < bordes[f + 1])
                mj = mejor_en(~test, coste, objetivo)
                if mj is None:
                    print(f"{f + 1:<8}  sin combinacion valida")
                    continue
                _, neto_in, ops_in, pairing, ventana, q, corte, dname, signo = mj
                got = puntuar(test, pairing, ventana, corte, signo, coste, "neto_total")
                neto_out, ops_out = (got[1], got[2]) if got else (0.0, 0)
                tot += neto_out
                tops += ops_out
                elig.append((pairing, ventana, q, dname))
                netos.append(neto_out)
                desc = f"{pairing.upper()} {ventana} q={q:.2f} ({corte:.4f}) {dname}"
                print(f"{f + 1:<8}{desc:>36}{neto_in:>10.2f}{ops_in:>7}"
                      f"{neto_out:>10.2f}{ops_out:>7}")
            print("-" * len(h))
            print(f"  NETO LOTO {tot:+.2f} % en {anios_tot} años = {tot / anios_tot:+.3f} %/año"
                  f"   |   {tops} ops, {tot / tops if tops else 0:+.6f} %/op"
                  f"   |   {sum(1 for x in netos if x > 0)}/{len(netos)} pliegues positivos")
            c = Counter(elig)
            print("  eleccion: " + " · ".join(
                f"{v}/10 {k[0].upper()} {k[1]} q={k[2]:.2f} {k[3]}"
                for k, v in sorted(c.items(), key=lambda x: -x[1])))




    # ---------------------------------------------------------------------
    # OPCION 2 de Walter: ventana creciente, estrictamente fuera de muestra.
    # "ventana optimizacion 50%, trade siguiente 10%, luego ventana 60% trade
    # 10%..." El LOTO entrena con datos POSTERIORES al bloque de test; esto no.
    # ---------------------------------------------------------------------
    print(f"\n\n{'#' * 96}\n##  OPCION 2 — ventana creciente (sin look-ahead), coste real por año\n{'#' * 96}")
    for objetivo in ("neto_total", "t_neto"):
        print(f"\nobjetivo dentro de muestra = {objetivo}")
        h = (f"{'entrena':<12}{'opera':<12}{'elegido':>36}{'FUERA':>10}{'ops':>7}")
        print(h); print("-" * len(h))
        tot, tops, netos = 0.0, 0, []
        for f in range(5, 10):
            train = idx < bordes[f]
            test = (idx >= bordes[f]) & (idx < bordes[f + 1])
            mj = mejor_en(train, coste_var, objetivo)
            if mj is None:
                continue
            _, _, _, pairing, ventana, q, corte, dname, signo = mj
            got = puntuar(test, pairing, ventana, corte, signo, coste_var, "neto_total")
            neto_out, ops_out = (got[1], got[2]) if got else (0.0, 0)
            tot += neto_out; tops += ops_out; netos.append(neto_out)
            desc = f"{pairing.upper()} {ventana} q={q:.2f} ({corte:.4f}) {dname}"
            print(f"{'0-' + str(f * 10) + '%':<12}{str(f * 10) + '-' + str(f * 10 + 10) + '%':<12}"
                  f"{desc:>36}{neto_out:>10.2f}{ops_out:>7}")
        print("-" * len(h))
        anios_mitad = anios_tot / 2
        print(f"  NETO {tot:+.2f} % sobre el ultimo 50% de la muestra (~{anios_mitad:.0f} años)"
              f" = {tot / anios_mitad:+.3f} %/año   |   {tops} ops"
              f"   |   {sum(1 for x in netos if x > 0)}/{len(netos)} bloques positivos")

    # ---------------------------------------------------------------------
    # Reparto por año del LOTO ganador (C, tramo previo, q=0.20, reversion),
    # para ver si el resultado vive de dos años como el del memorandum.
    # ---------------------------------------------------------------------
    print(f"\n\nREPARTO POR AÑO de la configuracion que elige el LOTO (C, tramo_prev, q=0.20, reversion)")
    corte_global = float(np.quantile(ER["tramo_prev"][np.isfinite(ER["tramo_prev"])], 0.20))
    sel = np.isfinite(ER["tramo_prev"]) & (ER["tramo_prev"] < corte_global)
    neto_seg = -pct["c"] - ops["c"] * coste_var
    por_anio = defaultdict(float); ops_anio = defaultdict(int)
    for j in range(m):
        if sel[j]:
            por_anio[int(anio[j])] += neto_seg[j]; ops_anio[int(anio[j])] += int(ops["c"][j])
    tot = sum(por_anio.values())
    print(f"  corte global equivalente: ER < {corte_global:.4f}   neto total {tot:+.2f} %")
    print(f"  {'año':<6}{'ops':>6}{'neto %':>10}{'% del total':>13}")
    for y in sorted(por_anio):
        print(f"  {y:<6}{ops_anio[y]:>6}{por_anio[y]:>10.2f}{100 * por_anio[y] / tot:>12.1f}%")
    pos = sum(1 for v in por_anio.values() if v > 0)
    top2 = sorted(por_anio.values(), reverse=True)[:2]
    print(f"  años positivos: {pos} de {len(por_anio)}   |   "
          f"los dos mejores aportan {100 * sum(top2) / tot:.0f}% del total")


if __name__ == "__main__":
    main()
