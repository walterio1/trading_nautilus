"""
¿Mejora ES el salto de fin de semana del E6? Se opera SOLO E6.

El modelo base es el validado en docs/memorandum-e6.md: desvanecer el movimiento
del E6 en los ultimos N minutos del viernes, mantener el fin de semana, deshacer
en la apertura. Aqui se añade como segundo input el movimiento de ES en esos
mismos N minutos (ES cierra a la misma hora que el E6 en Globex).

Con s_E6 y s_ES = signo del movimiento de cada uno en la ventana:

  base         posicion = -s_E6                   <- el modelo validado, sin ES
  solo_ES      posicion = -s_ES / +s_ES           (¿lidera la bolsa al euro?)
  acuerdo      base solo si E6 y ES se movieron en el mismo sentido, plano si no
  desacuerdo   base solo si se movieron en sentido contrario, plano si no

en las dos direcciones y con N de 5 minutos a 6 horas. Mismo listón que para
todo lo demas: ventana creciente estricta, comparando con el procedimiento
restringido al modelo base. Coste real del tick del E6 por año: una ida y
vuelta por fin de semana operado, nada si se queda plano.
"""

import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
E6 = str(DATA_DIR / "E6_full_1min_continuous_ratio_adjusted.txt")
ES = str(DATA_DIR / "ES_full_1min_continuous_ratio_adjusted.txt")
VENTANAS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360]
# ES no cerro siempre a la hora del E6 (16:59): hasta ~2015 lo hacia a las 16:14
# y en otra etapa a las 17:14. Su movimiento se mide en SUS ultimos N minutos
# hasta su ultimo precio a la hora del cierre del E6 o antes, que es causal
# (ya se conoce cuando cierra el E6). ANCLA_MAX acota esa antigüedad para no
# tomar el precio de un dia anterior cuando ES no abrio.
ANCLA_MAX = 60
# Tolerancia al buscar el precio N minutos antes del ancla. Tiene que cruzar la
# pausa diaria de ES de 16:15 a 16:30: con 5 minutos se perdian 418 fines de
# semana en N=30 y la muestra quedaba sesgada a los años de cierre a las 16:14.
MAX_DESFASE = 20
N_BLOQUES = 10


def cargar(path):
    minutos, apert, cierre, fechas = [], [], [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.split(",")
            if len(p) < 5:
                continue
            s = p[0]
            t = datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16]))
            minutos.append(t.toordinal() * 1440 + t.hour * 60 + t.minute)
            fechas.append(t)
            apert.append(float(p[1]))
            cierre.append(float(p[4]))
    return minutos, fechas, apert, cierre


def coste_por_anio(fechas, cierre):
    """Tick real en % de nominal por año, sobre barras de 1 minuto."""
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(cierre)):
        y = fechas[i].year
        if y == fechas[i - 1].year:
            d = abs(cierre[i] - cierre[i - 1])
            if d > 0:
                inc[y].append(round(d, 7))
        pre[y].append(cierre[i])
    out = {}
    for y, ds in inc.items():
        cand = sorted(v for v, k in Counter(ds).items() if k >= 0.01 * len(ds))
        out[y] = 100.0 * (cand[0] if cand else min(ds)) / (sum(pre[y]) / len(pre[y]))
    return out


def main():
    m6, f6, o6, c6 = cargar(E6)
    ms, _, _, cs = cargar(ES)
    coste_y = coste_por_anio(f6, c6)
    print(f"E6 {len(c6)} barras de 1 min, ES {len(cs)}")

    def es_en(minuto, tolerancia):
        """Indice del ultimo precio de ES a esa hora o hasta `tolerancia` minutos antes."""
        j = bisect_right(ms, minuto) - 1
        return j if j >= 0 and minuto - ms[j] <= tolerancia else None

    fines = []
    desfases = Counter()
    for i in range(1, len(c6)):
        if (m6[i] - m6[i - 1] > 40 * 60 and f6[i - 1].weekday() in (3, 4)
                and f6[i].weekday() in (6, 0) and i - 1 - max(VENTANAS) >= 0):
            t0 = m6[i - 1]
            ja = es_en(t0, ANCLA_MAX)
            if ja is None:
                continue
            ancla = ms[ja]
            reg = {"anio": f6[i - 1].year, "salto": (o6[i] / c6[i - 1] - 1) * 100,
                   "coste": coste_y[f6[i - 1].year]}
            ok = True
            for n in VENTANAS:
                jn = es_en(ancla - n, MAX_DESFASE)
                j6 = bisect_right(m6, t0 - n) - 1
                if jn is None or j6 < 0:
                    ok = False
                    break
                reg[("e6", n)] = c6[i - 1] / c6[j6] - 1
                reg[("es", n)] = cs[ja] / cs[jn] - 1
            if ok:
                fines.append(reg)
                desfases[t0 - ancla] += 1
    print(f"{len(fines)} fines de semana con precio de ES")
    print("  minutos entre el ultimo precio de ES y el cierre del E6: "
          + ", ".join(f"{k} min: {v}" for k, v in sorted(desfases.items())) + "\n")

    def sg(x):
        return 1 if x > 0 else (-1 if x < 0 else 0)

    reglas = []
    for n in VENTANAS:
        for d in (-1, 1):
            dn = "desvanecer" if d == -1 else "seguir"
            reglas.append((f"base {dn} E6", n, lambda w, n=n, d=d: d * sg(w[("e6", n)])))
            reglas.append((f"solo_ES {dn} ES", n, lambda w, n=n, d=d: d * sg(w[("es", n)])))
            reglas.append((f"acuerdo {dn} E6", n,
                           lambda w, n=n, d=d: d * sg(w[("e6", n)])
                           if sg(w[("e6", n)]) == sg(w[("es", n)]) else 0))
            reglas.append((f"desacuerdo {dn} E6", n,
                           lambda w, n=n, d=d: d * sg(w[("e6", n)])
                           if sg(w[("e6", n)]) == -sg(w[("es", n)]) != 0 else 0))

    F = len(fines)
    bordes = [F * k // N_BLOQUES for k in range(N_BLOQUES + 1)]

    def netos(regla):
        v = []
        for w in fines:
            p = regla[2](w)
            v.append(p * w["salto"] - (w["coste"] if p != 0 else 0.0))
        return v

    tabla = [(r, netos(r)) for r in reglas]
    base = [x for x in tabla if x[0][0].startswith("base desvanecer")]

    def resumen(v, ops):
        m = statistics.mean(v)
        t = m / (statistics.stdev(v) / len(v) ** 0.5)
        return sum(v), t, ops

    print("MUESTRA COMPLETA (orientativo) — las 10 mejores y el mejor modelo base")
    h = (f"  {'regla':<24}{'N':>5}{'opera':>7}{'NETO %':>9}{'t':>7}"
         f"{'1a mitad':>10}{'2a mitad':>10}{'%/año':>8}")
    print(h)
    print("  " + "-" * (len(h) - 2))
    anios = len({w["anio"] for w in fines})
    orden = sorted(tabla, key=lambda x: -sum(x[1]))
    mejor_base = max(base, key=lambda x: sum(x[1]))
    mostrar = orden[:10] + ([mejor_base] if mejor_base not in orden[:10] else [])
    for item in mostrar:
        r, v = item
        ops = sum(1 for w in fines if r[2](w) != 0)
        tot, t, _ = resumen(v, ops)
        mitad = len(v) // 2
        marca = "  <- modelo base" if item is mejor_base else ""
        print(f"  {r[0]:<24}{r[1]:>5}{ops:>7}{tot:>9.2f}{t:>+7.2f}"
              f"{sum(v[:mitad]):>10.2f}{sum(v[mitad:]):>10.2f}{tot / anios:>8.3f}{marca}")

    print(f"\n{'#' * 96}\nVENTANA CRECIENTE ESTRICTA — con ES frente al modelo base, "
          f"mismo procedimiento\n{'#' * 96}")
    h = f"  {'opera':<9}{'CON ES: elegido':<34}{'neto':>7}   {'BASE: elegido':<26}{'neto':>7}"
    print(h)
    print("  " + "-" * (len(h) - 2))
    tot = {"con": 0.0, "base": 0.0}
    pos = {"con": 0, "base": 0}
    gana = 0
    for k in range(5, N_BLOQUES):
        fila = f"  {str(k * 10) + '-' + str(k * 10 + 10) + '%':<9}"
        netos_k = {}
        for etq, pool in (("con", tabla), ("base", base)):
            r, v = max(pool, key=lambda x: sum(x[1][:bordes[k]]))
            n = sum(v[bordes[k]:bordes[k + 1]])
            netos_k[etq] = n
            tot[etq] += n
            pos[etq] += n > 0
            desc = f"{r[0]} N={r[1]}"
            ancho = 32 if etq == "con" else 24
            fila += f"{desc[:ancho]:<{ancho + 2}}{n:>7.2f}   "
        gana += netos_k["con"] > netos_k["base"]
        print(fila)
    anios_test = anios / 2
    print(f"  CON ES: {tot['con']:+.2f} % = {tot['con'] / anios_test:+.3f} %/año, {pos['con']}/5 +"
          f"   |   BASE: {tot['base']:+.2f} % = {tot['base'] / anios_test:+.3f} %/año, "
          f"{pos['base']}/5 +   |   ES mejora en {gana}/5 bloques")


if __name__ == "__main__":
    main()
