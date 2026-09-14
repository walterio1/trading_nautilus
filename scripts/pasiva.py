"""
Ejecucion PASIVA de la señal cruzada ES x E6, con ordenes limitadas.

Propuesta de Walter (2026-09-13):

    "si sale compra ponemos una orden de compra ligeramente por debajo del
     precio final de la señal, y esperamos durante los siguientes 7 minutos
     (mitad de los 15 minutos de plazo) hasta ver si se hace la transaccion,
     vendiendo al final de la barra de 15 minutos de la serie de trading
     (salvo que la serie de señal diga compra)."

MECANICA
  señal   : cierre de la barra de 15 min (:59, :14, :29, :44)
  limite  : cierre_señal - k ticks (compra) / + k ticks (venta)
  espera  : los 7 minutos siguientes al cierre de la señal
  salida  : al `fill_price` de la barra de 15 min siguiente -un minuto despues
            de su cierre, como el resto del proyecto-, salvo que la señal siga
            diciendo lo mismo, en cuyo caso se mantiene
  coste   : la ENTRADA no paga spread (es pasiva, y ademas entra k ticks mejor);
            la SALIDA es agresiva y paga medio tick

LA REGLA DE RELLENO — la fijo Walter (2026-09-13):

    "si el minimo de los siguientes 7 minutos es inferior al precio limite de
     compra, la ejecucion es a este ultimo"

o sea `estricta`: hace falta que el precio ATRAVIESE el limite, no que lo
toque. Es la conservadora y la correcta: que se opere justo en tu nivel no
significa que te llenen a ti -estas al final de una cola de miles de contratos-,
mientras que si alguien ha operado POR DEBAJO de tu precio, tu orden tenia que
haber salido antes.

Se calcula tambien `optimista` (low <= limite) para tener la cota superior a la
vista: la distancia entre las dos ES el riesgo de cola, y en la epoca moderna
resulta ser la diferencia entre un negocio y nada.

LO QUE DE VERDAD HAY QUE MIRAR: LA SELECCION ADVERSA
  Se compara el rendimiento posterior de TODAS las señales contra el de las
  señales QUE SE LLENARON. Si llenarse cuesta rendimiento -y cuesta, porque solo
  te llenan cuando el precio sigue yendo en tu contra- la diferencia sale aqui y
  no en una nota al pie. Un ahorro de k ticks en el precio de entrada no vale
  nada si la seleccion adversa se come mas de k ticks de rendimiento.
"""

import csv
from collections import Counter, defaultdict

import numpy as np

ES_MIN = "/mnt/user-data/uploads/trading_nautilus/data/ES_full_1min_continuous_ratio_adjusted.txt"
ES_15 = "/home/claude/es/ES_pnl_lagged_fast.csv"
E6_15 = "/mnt/user-data/uploads/trading_nautilus/data/E6_pnl_lagged_fast.csv"
VENTANAS = ("n_fast", "n_slow", "n_super")
ESPERA = 7          # minutos de vida de la orden limitada
TICKS = (1, 2, 3, 4)


def clave(s):
    """'YYYY-MM-DD HH:MM:SS' -> minutos absolutos, para indexar sin strings."""
    return (((int(s[:4]) * 12 + int(s[5:7])) * 31 + int(s[8:10])) * 24
            + int(s[11:13])) * 60 + int(s[14:16])


def cargar_15(path):
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


def cargar_min():
    k, lo, hi, cl = [], [], [], []
    with open(ES_MIN, encoding="utf-8") as f:
        for line in f:
            p = line.split(",")
            if len(p) < 5:
                continue
            k.append(clave(p[0]))
            hi.append(float(p[2]))
            lo.append(float(p[3]))
            cl.append(float(p[4]))
    return np.array(k, dtype=np.int64), np.array(lo), np.array(hi), np.array(cl)


def rejilla(ts, close):
    """grano de la rejilla y coste en % de nominal, por año."""
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(close)):
        if ts[i][:4] == ts[i - 1][:4]:
            d = abs(close[i] - close[i - 1])
            if d > 0:
                inc[ts[i][:4]].append(round(d, 7))
        pre[ts[i][:4]].append(close[i])
    grano, coste = {}, {}
    for y, ds in inc.items():
        c = Counter(ds)
        cand = sorted(v for v, n in c.items() if n >= 0.01 * len(ds))
        g = cand[0] if cand else min(ds)
        grano[y] = g
        coste[y] = 100.0 * g / (sum(pre[y]) / len(pre[y]))
    return grano, coste


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


def main():
    ts_es, cl_es, fi_es, n_es = cargar_15(ES_15)
    ts_e6, cl_e6, fi_e6, n_e6 = cargar_15(E6_15)
    grano, coste_y = rejilla(ts_es, cl_es)
    km, lo_m, hi_m, cl_m = cargar_min()
    print(f"{len(km)} barras de 1 minuto del ES cargadas")

    ix = {t: i for i, t in enumerate(ts_e6)}
    par = [(i, ix[t]) for i, t in enumerate(ts_es) if t in ix]
    a = np.array([i for i, _ in par])
    b = np.array([j for _, j in par])
    T = len(a)
    anio = np.array([int(ts_es[i][:4]) for i in a])
    tick = np.array([grano[str(y)] for y in anio])          # tick en unidades de precio
    coste = np.array([coste_y[str(y)] for y in anio])       # un tick en % de nominal

    sE, eE = signo_y_er(cl_es, n_es["n_super"])
    s6, _ = signo_y_er(cl_e6, n_e6["n_super"])
    sE, eE, s6 = sE[a], eE[a], s6[b]

    # la configuracion que el LOTO elige solo: caso C (ES debil / E6 fuerte),
    # ventana n_super, filtro er_alto al 10%, largo en ES.
    corte = float(np.quantile(eE[np.isfinite(eE)], 0.10))
    señal = np.isfinite(eE) & (eE > corte) & (sE == -1) & (s6 == 1)
    print(f"señal de compra en {señal.sum()} de {T} barras "
          f"({100 * señal.sum() / T:.1f}%), corte ER > {corte:.4f}")

    # posicion del minuto siguiente al cierre de cada barra de señal
    k0 = np.array([clave(ts_es[i]) for i in a]) + 1
    pos_min = np.searchsorted(km, k0)

    cl15 = cl_es[a]
    fill15 = fi_es[a]

    # --- referencia agresiva: entrar al fill_price, pagar medio tick ---------
    entradas = np.where(señal & ~np.concatenate([[False], señal[:-1]]))[0]
    # salida: primera barra en que la señal deja de decir compra
    sal = np.empty(len(entradas), dtype=int)
    for j, e in enumerate(entradas):
        k = e + 1
        while k < T and señal[k]:
            k += 1
        sal[j] = min(k, T - 1)
    print(f"{len(entradas)} señales de entrada, duracion media "
          f"{np.mean(sal - entradas):.1f} barras de 15 min")

    ret_ag = (fill15[sal] / fill15[entradas] - 1) * 100 - coste[entradas]
    print(f"\nAGRESIVA (lo medido hasta ahora): neto medio "
          f"{ret_ag.mean():+.5f} % por operacion, total {ret_ag.sum():+.2f} %")

    print(f"\n{'=' * 100}")
    print(f"PASIVA — limite k ticks por debajo del cierre de la señal, "
          f"{ESPERA} minutos de espera")
    print(f"{'=' * 100}")
    h = (f"{'k':<3}{'regla':<11}{'llenadas':>10}{'% lleno':>9}"
         f"{'mejora entrada':>16}{'ret bruto':>11}{'neto %/op':>11}"
         f"{'total %':>10}{'sel.adversa':>13}")
    print(h)
    print("-" * len(h))
    for k in TICKS:
        for regla in ("optimista", "estricta"):
            lim = cl15[entradas] - k * tick[entradas]
            lleno = np.zeros(len(entradas), dtype=bool)
            for d in range(ESPERA):
                p = pos_min[entradas] + d
                # La orden solo esta viva si ese minuto EXISTE y es el minuto
                # que toca: saltar por indice cruzaria los cortes de sesion y
                # daria por relleno el minimo de la sesion siguiente, horas
                # despues. Sin esta guarda la pasiva sale regalada.
                ok = (p < len(lo_m))
                ok[ok] &= (km[p[ok]] == k0[entradas][ok] + d)
                bajo = np.zeros(len(entradas), dtype=bool)
                if regla == "optimista":
                    bajo[ok] = lo_m[p[ok]] <= lim[ok] + 1e-12
                else:
                    bajo[ok] = lo_m[p[ok]] < lim[ok] - 1e-12
                lleno |= bajo
            n_ll = int(lleno.sum())
            if n_ll < 50:
                continue
            e_ll = entradas[lleno]
            s_ll = sal[lleno]
            # entrada al limite, salida agresiva pagando medio tick
            bruto = (fill15[s_ll] / lim[lleno] - 1) * 100
            neto = bruto - coste[e_ll] / 2
            # mejora de precio de entrada frente a la agresiva, en % de nominal
            mejora = (fill15[e_ll] / lim[lleno] - 1) * 100
            # seleccion adversa: rendimiento posterior de las llenadas frente
            # al de TODAS las señales, ambos medidos desde el MISMO precio
            # (el fill agresivo), asi que la diferencia es solo el sesgo de
            # que te llenen, no el mejor precio de entrada.
            r_todas = (fill15[sal] / fill15[entradas] - 1) * 100
            r_llenas = (fill15[s_ll] / fill15[e_ll] - 1) * 100
            sel = r_llenas.mean() - r_todas.mean()
            print(f"{k:<3}{regla:<11}{n_ll:>10}{100 * n_ll / len(entradas):>8.1f}%"
                  f"{mejora.mean():>+16.5f}{bruto.mean():>+11.5f}"
                  f"{neto.mean():>+11.5f}{neto.sum():>+10.2f}{sel:>+13.5f}")

    print("\n  'mejora entrada' = cuanto mejor compras que con orden a mercado, "
          "en % de nominal.")
    print("  'sel.adversa'    = rendimiento posterior de las llenadas MENOS el de "
          "todas las señales,")
    print("                     medido desde el mismo precio. Negativo = llenarte "
          "te cuesta rendimiento.")
    print("  La pasiva gana si 'mejora entrada' supera a la 'sel.adversa' en valor "
          "absoluto.")


if __name__ == "__main__":
    main()
