"""
¿Mejora ES la prevision del E6? Se opera SOLO E6; ES entra solo como input.

Planteamiento de Walter (2026-09-14): la sesion anterior uso E6 para mejorar
la prevision de ES y no salio bien; aqui lo opuesto. La señal combina el signo
de E6 y el de ES, calculado de dos formas:

  INCREMENTO   signo de close(t) - close(t - N) sobre la ventana endogena N,
               con N de E6 para los dos, N de ES para los dos, o cada uno la
               suya (las tres variantes, en n_fast / n_slow / n_super).
  MEDIAS       signo del cruce de medias de cada instrumento (pares A, B, C
               de pnl_series.py), al estilo de lo ya hecho.

Con los dos signos (s_E6, s_ES) se prueban reglas simetricas e interpretables,
cada una en las dos direcciones:

  solo_E6      posicion = s_E6                  <- MODELO BASE, sin ES
  solo_ES      posicion = s_ES                  (ES lidera al E6)
  acuerdo      s_E6 cuando coinciden, plano si no
  desacuerdo   s_E6 cuando discrepan, plano si no
  caso_A..D    largo solo en uno de los cuatro casos (ES,E6) =
               (debil,debil) (fuerte,fuerte) (debil,fuerte) (fuerte,debil)

El listón es la VENTANA CRECIENTE ESTRICTA (entrenar 0-50 %, operar 50-60 %,
entrenar 0-60 %, operar 60-70 %...), la que dejo fuera de juego a la estrategia
ER aunque el LOTO la aprobaba. Y la pregunta no es "¿gana dinero?" sino
"¿gana MAS que el mismo procedimiento restringido a solo_E6?": el optimizador
elige una vez entre todas las reglas y otra vez solo entre las solo_E6, con los
mismos datos; ES aporta si y solo si la primera supera a la segunda fuera.

Coste: tick REAL del E6 por año, medido de la rejilla de la serie, medio tick
por unidad de cambio de posicion (dar la vuelta = un tick, entrar y salir a
plano = un tick entre las dos). Ejecucion fill-a-fill: señal en el cierre de
15 min, operacion al minuto siguiente. Todo en % de nominal del E6.
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
E6 = str(DATA_DIR / "E6_pnl_lagged_fast.csv")
ES = str(DATA_DIR / "ES_pnl_lagged_fast.csv")
VENTANAS = ("n_fast", "n_slow", "n_super")
PARES_MM = ("a", "b", "c")
N_BLOQUES = 10
MIN_VUELTAS = 50        # idas y vueltas minimas en entrenamiento para puntuar


def cargar(path):
    ts, close, fill = [], [], []
    ns = {k: [] for k in VENTANAS}
    pos = {k: [] for k in PARES_MM}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts.append(row["timestamp"])
            close.append(float(row["close"]))
            fill.append(float(row["fill_price"]))
            for k in VENTANAS:
                ns[k].append(int(row[k] or 0))
            for k in PARES_MM:
                pos[k].append(int(row[f"pos_{k}"] or 0))
    return (ts, np.array(close), np.array(fill),
            {k: np.array(v) for k, v in ns.items()},
            {k: np.array(v) for k, v in pos.items()})


def coste_por_anio(ts, close):
    """Tick real en % de nominal, año a año (misma medida que loto_e6.py)."""
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(close)):
        if ts[i][:4] == ts[i - 1][:4]:
            d = abs(close[i] - close[i - 1])
            if d > 0:
                inc[ts[i][:4]].append(round(d, 7))
        pre[ts[i][:4]].append(close[i])
    out = {}
    for y, ds in inc.items():
        cand = sorted(v for v, k in Counter(ds).items() if k >= 0.01 * len(ds))
        out[y] = 100.0 * (cand[0] if cand else min(ds)) / (sum(pre[y]) / len(pre[y]))
    return out


def signo_incremento(close, n):
    """signo de log(close_t / close_{t-n_t}); 0 donde la ventana no cabe."""
    m = len(close)
    t = np.arange(m)
    w = n.astype(int)
    ok = (w >= 2) & (t - w >= 0)
    ref = np.where(ok, t - w, 0)
    d = np.where(ok, np.log(close / close[ref]), 0.0)
    return np.sign(d).astype(np.int8)


def main():
    ts6, cl6, fi6, n6, p6 = cargar(E6)
    tss, cls, _, ns, ps = cargar(ES)
    coste_y = coste_por_anio(ts6, cl6)

    # Linea temporal comun: solo se decide en barras donde existen los dos.
    ix = {t: i for i, t in enumerate(tss)}
    par = [(i, ix[t]) for i, t in enumerate(ts6) if t in ix]
    a = np.array([i for i, _ in par])          # indices E6
    b = np.array([j for _, j in par])          # indices ES
    T = len(a)
    ts = [ts6[i] for i in a]
    anio = np.array([int(t[:4]) for t in ts])
    print(f"{T} barras comunes E6/ES, {ts[0][:10]} a {ts[-1][:10]}")

    # Rendimiento del E6 de ejecucion a ejecucion ENTRE barras comunes: si se
    # salta una barra de E6 sin ES, su movimiento sigue dentro del rendimiento.
    ret = np.zeros(T)
    ret[:-1] = (fi6[a[1:]] / fi6[a[:-1]] - 1) * 100
    coste = np.array([coste_y[str(y)] for y in anio])
    print(f"coste medio de un tick del E6: {coste.mean():.5f} % de nominal")

    # Las ventanas se miden en barras comunes, lo que permite aplicar la N de
    # un instrumento a la serie del otro sin ambigüedad.
    c6, cs = cl6[a], cls[b]
    familias = {}
    for w in VENTANAS:
        N6, NS = n6[w][a], ns[w][b]
        familias[f"inc propia {w}"] = (signo_incremento(c6, N6), signo_incremento(cs, NS))
        familias[f"inc N_E6 {w}"] = (signo_incremento(c6, N6), signo_incremento(cs, N6))
        familias[f"inc N_ES {w}"] = (signo_incremento(c6, NS), signo_incremento(cs, NS))
    for k in PARES_MM:
        familias[f"medias par {k.upper()}"] = (p6[k][a].astype(np.int8), ps[k][b].astype(np.int8))

    def reglas(s6, se):
        return {
            "solo_E6": s6,
            "solo_ES": se,
            "acuerdo": np.where(s6 == se, s6, 0),
            "desacuerdo": np.where((s6 == -se) & (s6 != 0), s6, 0),
            "caso_A deb/deb": ((se == -1) & (s6 == -1)).astype(np.int8),
            "caso_B fue/fue": ((se == 1) & (s6 == 1)).astype(np.int8),
            "caso_C ESdeb/E6fue": ((se == -1) & (s6 == 1)).astype(np.int8),
            "caso_D ESfue/E6deb": ((se == 1) & (s6 == -1)).astype(np.int8),
        }

    bordes = [T * k // N_BLOQUES for k in range(N_BLOQUES + 1)]
    bloque = np.zeros(T, dtype=int)
    for k in range(N_BLOQUES):
        bloque[bordes[k]:bordes[k + 1]] = k
    anios = sorted(set(anio))

    cand = []   # (familia, regla, direccion, bruto[bloques], coste[bloques], giro[bloques], neto_anio)
    for fam, (s6, se) in familias.items():
        for rnom, r in reglas(s6, se).items():
            # Duplicados exactos: con N propia, E6 ya usa N_E6 y ES ya usa N_ES.
            if (rnom == "solo_E6" and fam.startswith("inc N_E6")) or \
               (rnom == "solo_ES" and fam.startswith("inc N_ES")):
                continue
            for dnom, d in (("tendencia", 1.0), ("reversion", -1.0)):
                pos = d * r.astype(float)
                giro = np.abs(np.diff(np.concatenate([[0.0], pos])))
                pnl = pos * ret
                cst = giro * coste / 2
                bru_b = np.bincount(bloque, pnl, N_BLOQUES)
                cst_b = np.bincount(bloque, cst, N_BLOQUES)
                gir_b = np.bincount(bloque, giro, N_BLOQUES)
                neto_y = {y: float((pnl - cst)[anio == y].sum()) for y in anios}
                cand.append((fam, rnom, dnom, bru_b, cst_b, gir_b, neto_y))

    base = [c for c in cand if c[1] == "solo_E6"]
    print(f"{len(cand)} reglas candidatas, de ellas {len(base)} sin ES (modelo base)\n")

    # ---------------------------------------------------------------- muestra completa
    def fila_completa(c):
        bru, cst, gir = c[3].sum(), c[4].sum(), c[5].sum()
        vueltas = gir / 2
        neto = bru - cst
        n1 = float((c[3] - c[4])[:5].sum())
        n2 = float((c[3] - c[4])[5:].sum())
        xc = (bru / vueltas / coste.mean()) if vueltas else 0.0
        pos_y = sum(1 for v in c[6].values() if v > 0)
        return neto, n1, n2, vueltas, xc, pos_y

    print(f"MUESTRA COMPLETA (elegido mirando todo, {len(cand)} reglas: solo orientativo)")
    h = (f"  {'familia':<20}{'regla':<20}{'dir':<10}{'vueltas':>8}{'x coste':>8}"
         f"{'NETO %':>9}{'1a mitad':>9}{'2a mitad':>9}{'años+':>7}")
    print(h)
    print("  " + "-" * (len(h) - 2))
    orden = sorted(cand, key=lambda c: -fila_completa(c)[0])
    mostrar = orden[:12]
    mejor_base = max(base, key=lambda c: fila_completa(c)[0])
    if mejor_base not in mostrar:
        mostrar.append(mejor_base)
    for c in mostrar:
        neto, n1, n2, vu, xc, py = fila_completa(c)
        marca = "  <- mejor sin ES" if c is mejor_base else ""
        print(f"  {c[0]:<20}{c[1]:<20}{c[2]:<10}{vu:>8.0f}{xc:>7.2f}x"
              f"{neto:>9.2f}{n1:>9.2f}{n2:>9.2f}{py:>4}/{len(anios)}{marca}")

    # ---------------------------------------------------------------- ventana creciente
    def elegir(pool, k, objetivo):
        mejor, puntos = None, None
        for c in pool:
            vu = c[5][:k].sum() / 2
            if vu < MIN_VUELTAS:
                continue
            neto = float((c[3] - c[4])[:k].sum())
            score = neto if objetivo == "neto_total" else neto / vu
            if puntos is None or score > puntos:
                mejor, puntos = c, score
        return mejor

    print(f"\n\n{'#' * 100}\nVENTANA CRECIENTE ESTRICTA — con ES frente a sin ES, "
          f"elegidos con el mismo procedimiento\n{'#' * 100}")
    resumen = {}
    for objetivo in ("neto_total", "neto_por_vuelta"):
        print(f"\nobjetivo dentro de muestra = {objetivo}")
        h = f"  {'opera':<9}{'CON ES: elegido':<48}{'neto':>7}   {'SIN ES: elegido':<34}{'neto':>7}"
        print(h)
        print("  " + "-" * (len(h) - 2))
        tot = {"con": 0.0, "sin": 0.0}
        pos = {"con": 0, "sin": 0}
        gana = 0
        for k in range(5, N_BLOQUES):
            fila = f"  {str(k * 10) + '-' + str(k * 10 + 10) + '%':<9}"
            netos = {}
            for etq, pool in (("con", cand), ("sin", base)):
                c = elegir(pool, k, objetivo)
                n = float(c[3][k] - c[4][k]) if c else 0.0
                netos[etq] = n
                tot[etq] += n
                pos[etq] += n > 0
                desc = f"{c[0]} · {c[1]} · {c[2]}" if c else "sin candidato"
                ancho = 46 if etq == "con" else 32
                fila += f"{desc[:ancho]:<{ancho + 2}}{n:>7.2f}   "
            gana += netos["con"] > netos["sin"]
            print(fila)
        anios_test = (len(anios)) / 2
        print(f"  CON ES: {tot['con']:+.2f} % = {tot['con'] / anios_test:+.3f} %/año, "
              f"{pos['con']}/5 bloques +   |   SIN ES: {tot['sin']:+.2f} % = "
              f"{tot['sin'] / anios_test:+.3f} %/año, {pos['sin']}/5 bloques +   |   "
              f"ES mejora en {gana}/5 bloques")
        resumen[objetivo] = (tot, pos, gana)

    print(f"\nCoste: tick real del E6 por año. Rendimientos fill-a-fill. "
          f"{len(cand)} reglas; la seleccion se hace dentro de cada ventana de entrenamiento.")


if __name__ == "__main__":
    main()
