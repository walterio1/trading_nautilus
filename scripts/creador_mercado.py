"""
CREADOR DE MERCADO DE DOS LADOS — propuesta de Walter (2026-09-14).

    "al cierre del minuto t=0 hallas hi-lo como margen de seguridad. Y pones una
     orden limitada de compra a un precio igual al cierre de t=0 menos ese
     margen, asi como una orden limitada de venta al cierre de t=0 mas ese
     margen. Esas ordenes entran justo despues del cierre de t=0 y estan
     vigentes en el minuto t=1. Si cualquiera de ellas se cruza (y no se cancela
     con la otra dentro del minuto t=1) entonces pones la orden inversa, tambien
     limitada, y asi hasta que se cierre."
    "si hi=lo en una barra, se retrocede lo menos posible en el tiempo, barra a
     barra, hasta encontrar una barra previa en que hi>lo y se pone ese hi-lo"

Esto NO tiene señal direccional: es cotizar a dos lados y deshacer. O sea hacer
de creador de mercado, cobrando por dar inmediatez en vez de pagarla.

MECANICA EXACTA
  plano   : margen m = hi - lo de la barra en curso (retrocediendo si hi==lo);
            compra limitada a cl - m y venta limitada a cl + m, vivas la barra
            siguiente
  llenado : si solo se cruza una, queda la posicion. Si se cruzan LAS DOS en el
            mismo minuto, has comprado a cl-m y vendido a cl+m, asi que quedas
            plano con +2m ganados, sea cual sea el orden en que ocurrieron
  deshacer: con posicion, se cotiza solo el lado contrario, a cl +- m de la
            barra en curso, y se repite barra a barra hasta que se llene. Puede
            cerrarse EN PERDIDA -asi lo planteo Walter y asi esta-, no se espera
            a estar en beneficio

REGLA DE RELLENO: la de Walter, la estricta. El precio tiene que ATRAVESAR el
limite (lo < limite de compra, hi > limite de venta), no tocarlo. Se da tambien
la optimista (<=, >=) como cota superior: la distancia entre las dos es el
riesgo de cola.

DOS SESGOS QUE VAN A FAVOR DE LA ESTRATEGIA Y QUE AQUI SE EVITAN
  * si el minuto abre por DEBAJO de la compra limitada, en la realidad te
    llenan al precio de apertura, mejor que tu limite. Aqui siempre se llena AL
    LIMITE, o sea peor. Conservador a proposito.
  * saltar de barra por indice cruzaria los cortes de sesion y daria por
    vigente una orden horas despues. Se comprueba que el minuto siguiente
    EXISTE y es el que toca.

UN AVISO SOBRE LA REJILLA, que me costo un resultado falso. Los precios del
ES ajustado por ratio vienen REDONDEADOS A 2 DECIMALES sobre un grano real de
~0.2575 que ademas cambia en cada vencimiento trimestral. Si uno estima el tick
por año y luego coloca los limites en `round(margen/tick)*tick`, acaba cotizando
en precios QUE NO EXISTEN, medio grano mejores que el real -y eso fabrica
beneficio de la nada: en mi primera pasada daba +7.038 % donde la version
correcta da -1.972 %-. Aqui los limites son siempre `cl +- k*(hi-lo)` con k
entero, que por construccion cae en precios impresos de verdad.

EL COSTE AQUI NO ES EL SPREAD. Las dos patas son pasivas, asi que no se cruza
horquilla ninguna: lo unico que se paga son COMISIONES. Y esta estrategia opera
much0, asi que la comision manda. Se reporta a 0, 2 y 4 USD por ida y vuelta.
"""

import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATOS = str(DATA_DIR / "ES_full_1min_continuous_ratio_adjusted.txt")


def clave(s):
    return (((int(s[:4]) * 12 + int(s[5:7])) * 31 + int(s[8:10])) * 24
            + int(s[11:13])) * 60 + int(s[14:16])


def cargar():
    k, op, hi, lo, cl, an = [], [], [], [], [], []
    with open(DATOS, encoding="utf-8") as f:
        for line in f:
            p = line.split(",")
            if len(p) < 5:
                continue
            k.append(clave(p[0]))
            an.append(int(p[0][:4]))
            op.append(float(p[1]))
            hi.append(float(p[2]))
            lo.append(float(p[3]))
            cl.append(float(p[4]))
    return (np.array(k, dtype=np.int64), np.array(op), np.array(hi),
            np.array(lo), np.array(cl), np.array(an))


def rejilla_anual(an, cl):
    """tick y coste en % de nominal por año, con el factor del ajuste cancelado."""
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(cl)):
        if an[i] == an[i - 1]:
            d = abs(cl[i] - cl[i - 1])
            if d > 0:
                inc[an[i]].append(round(d, 7))
        pre[an[i]].append(cl[i])
    tick, coste = {}, {}
    for y, ds in inc.items():
        c = Counter(ds)
        cand = sorted(v for v, n in c.items() if n >= 0.01 * len(ds))
        g = cand[0] if cand else min(ds)
        tick[y] = g
        coste[y] = 100.0 * g / (sum(pre[y]) / len(pre[y]))
    return tick, coste


def margenes(hi, lo):
    """m(t) = hi-lo, y si es cero se retrocede lo menos posible."""
    m = hi - lo
    ult = 0.0
    out = np.empty_like(m)
    for i in range(len(m)):
        if m[i] > 0:
            ult = m[i]
        out[i] = ult          # 0.0 solo si aun no ha habido ninguna barra con rango
    return out


def simular(k, op, hi, lo, cl, m, estricta=True):
    """
    Recorre la serie. Devuelve la lista de operaciones cerradas:
    (indice_entrada, indice_salida, lado, precio_entrada, precio_salida).
    """
    n = len(cl)
    ops = []
    pos = 0          # 0 plano, +1 largo, -1 corto
    p_ent = 0.0
    i_ent = 0
    dobles = 0
    for t in range(n - 1):
        if m[t] <= 0:
            continue
        s = t + 1
        if k[s] != k[t] + 1:      # corte de sesion: la orden no llega a estar viva
            continue
        compra = cl[t] - m[t]
        venta = cl[t] + m[t]
        if estricta:
            toca_c = lo[s] < compra
            toca_v = hi[s] > venta
        else:
            toca_c = lo[s] <= compra
            toca_v = hi[s] >= venta

        if pos == 0:
            if toca_c and toca_v:
                # las dos se llenan: comprado a cl-m y vendido a cl+m -> plano
                ops.append((s, s, +1, compra, venta))
                dobles += 1
            elif toca_c:
                pos, p_ent, i_ent = +1, compra, s
            elif toca_v:
                pos, p_ent, i_ent = -1, venta, s
        elif pos == +1:
            if toca_v:
                ops.append((i_ent, s, +1, p_ent, venta))
                pos = 0
        else:
            if toca_c:
                ops.append((i_ent, s, -1, p_ent, compra))
                pos = 0
    return ops, dobles


def main():
    k, op, hi, lo, cl, an = cargar()
    print(f"{len(cl)} barras de 1 minuto del ES")
    tick, coste = rejilla_anual(an, cl)
    m = margenes(hi, lo)
    ceros = np.mean((hi - lo) == 0)
    print(f"barras con hi==lo (se hereda el margen anterior): {100 * ceros:.1f} %")
    print(f"margen medio: {np.mean(m[m > 0]):.4f} en unidades de precio = "
          f"{np.mean([m[i] / cl[i] for i in range(0, len(cl), 97) if m[i] > 0]) * 100:.4f} % de nominal")
    tm = np.mean([tick[an[i]] for i in range(0, len(cl), 997)])
    print(f"margen medio en ticks: ~{np.mean(m[m > 0]) / tm:.2f}")

    for etiqueta, estricta in (("ESTRICTA (la de Walter: el precio atraviesa)", True),
                               ("optimista (basta con tocar)", False)):
        ops, dobles = simular(k, op, hi, lo, cl, m, estricta)
        if not ops:
            print(f"\n{etiqueta}: sin operaciones")
            continue
        i_e = np.array([o[0] for o in ops])
        i_s = np.array([o[1] for o in ops])
        lado = np.array([o[2] for o in ops])
        p_e = np.array([o[3] for o in ops])
        p_s = np.array([o[4] for o in ops])
        pnl = lado * (p_s / p_e - 1) * 100          # % de nominal, sin costes
        dur = k[i_s] - k[i_e]
        anio = an[i_e]
        años = sorted(set(anio))

        print(f"\n{'=' * 96}\n{etiqueta}\n{'=' * 96}")
        print(f"  {len(ops)} operaciones ({len(ops) / len(años):.0f}/año, "
              f"{dobles} de ellas con los dos lados llenos en el mismo minuto)")
        print(f"  duracion: mediana {np.median(dur):.0f} min, media {np.mean(dur):.1f} min, "
              f"p90 {np.quantile(dur, 0.9):.0f} min, maxima {dur.max():.0f} min")
        print(f"  BRUTO total {pnl.sum():+.2f} % de nominal · "
              f"{pnl.mean():+.6f} %/op · t={pnl.mean() / (pnl.std(ddof=1) / len(pnl) ** 0.5):+.1f}")
        print(f"  aciertos {100 * np.mean(pnl > 0):.1f} %")

        # en dolares, por epoca, con comisiones
        print(f"\n  {'epoca':<12}{'ops':>9}{'ops/año':>9}{'indice':>9}{'nominal':>11}"
              f"{'bruto $/op':>12}{'bruto $/año':>13}{'con 2 $':>12}{'con 4 $':>12}")
        for etq, sel in (("2008-2016", anio <= 2016), ("2017-2025", anio >= 2017),
                         ("todo", np.ones(len(ops), bool))):
            ny = len(set(anio[sel]))
            nops = int(sel.sum())
            idx = 0.25 / (np.mean([coste[y] for y in anio[sel]]) / 100)
            nom = 50 * idx
            b_op = pnl[sel].mean() / 100 * nom
            b_an = pnl[sel].sum() / 100 * nom / ny
            print(f"  {etq:<12}{nops:>9}{nops / ny:>9.0f}{idx:>9.0f}{nom:>11,.0f}"
                  f"{b_op:>12.2f}{b_an:>13,.0f}"
                  f"{b_an - (nops / ny) * 2:>12,.0f}{b_an - (nops / ny) * 4:>12,.0f}")

        print(f"\n  {'año':<7}{'ops':>8}{'bruto %':>11}{'%/op':>11}")
        for y in años:
            s = anio == y
            print(f"  {y:<7}{int(s.sum()):>8}{pnl[s].sum():>+11.2f}{pnl[s].mean():>+11.6f}")


def barrido_anchura():
    """Anchuras que caen siempre en precios impresos: cl +- k*(hi-lo), k entero.

    Se separa ademas lo que aportan los minutos en que se llenan LOS DOS lados,
    porque con OHLC no se puede saber si de verdad se ejecutaron las dos ordenes
    -solo que el precio paso por los dos niveles-."""
    k, op, hi, lo, cl, an = cargar()
    base = margenes(hi, lo)
    print(f"\n{'=' * 96}\nANCHURA DE LA COTIZACION (regla estricta)\n{'=' * 96}")
    print(f"{'k':>3}{'ops':>10}{'bruto %':>11}{'%/op':>11}{'t':>8}"
          f"{'acierto':>9}{'1a mitad':>11}{'2a mitad':>11}{'sin dobles':>12}{'t':>7}")
    for mult in (1, 2, 3, 4):
        n = len(cl); ops = []; pos = 0; pe = 0.0; ie = 0
        for t in range(n - 1):
            s2 = t + 1
            if k[s2] != k[t] + 1 or base[t] <= 0:
                continue
            c, v = cl[t] - mult * base[t], cl[t] + mult * base[t]
            tc, tv = lo[s2] < c, hi[s2] > v
            if pos == 0:
                if tc and tv: ops.append((+1, c, v, True))
                elif tc: pos, pe = +1, c
                elif tv: pos, pe = -1, v
            elif pos == +1:
                if tv: ops.append((+1, pe, v, False)); pos = 0
            else:
                if tc: ops.append((-1, pe, c, False)); pos = 0
        ld = np.array([o[0] for o in ops]); p_e = np.array([o[1] for o in ops])
        p_s = np.array([o[2] for o in ops]); db = np.array([o[3] for o in ops])
        p = ld * (p_s / p_e - 1) * 100          # SIEMPRE en %, nunca en puntos
        pr = p[~db]; mit = len(p) // 2
        tt = p.mean() / (p.std(ddof=1) / len(p) ** 0.5)
        tr = pr.mean() / (pr.std(ddof=1) / len(pr) ** 0.5)
        print(f"{mult:>3}{len(ops):>10}{p.sum():>+11.1f}{p.mean():>+11.6f}{tt:>+8.1f}"
              f"{100 * np.mean(p > 0):>8.1f}%{p[:mit].sum():>+11.1f}{p[mit:].sum():>+11.1f}"
              f"{pr.sum():>+12.1f}{tr:>+7.1f}")


if __name__ == "__main__":
    main()
    barrido_anchura()
