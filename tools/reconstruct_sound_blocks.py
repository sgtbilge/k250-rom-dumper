#!/usr/bin/env python3
"""
Computationally reconstructs CR-stripped EPROM dumps for Kurzweil K250 Sound Block D
and Sound Block C using deterministic structural/metadata alignment + a compiled C11
exact-count coupled even/odd Viterbi solver for waveform regions.

Original dumps in "K250 Sound Block D" and "K250 Sound Block C" are NEVER modified;
outputs are written to "K250 Sound Block D (Reconstructed)" and
"K250 Sound Block C (Reconstructed)".
"""

import argparse
import os
import struct
import subprocess
import tempfile
import sys

ROMS_BASE = os.environ.get("K250_SOUNDS_BINARIES_DIR", ".")
DIR_D_IN = os.path.join(ROMS_BASE, "K250 Sound Block D")
DIR_C_IN = os.path.join(ROMS_BASE, "K250 Sound Block C")
DIR_D_OUT = os.path.join(ROMS_BASE, "K250 Sound Block D (Reconstructed)")
DIR_C_OUT = os.path.join(ROMS_BASE, "K250 Sound Block C (Reconstructed)")

EXPECTED_D = {
    "098K250": 0x1BFC,
    "099K250": 0x2988,
    "100K250": 0x4B75,
    "101K250": 0xC59B,
    "102K250": 0x6FFD,
    "103K250": 0xD191,
    "104K250": 0x20EB,
    "105K250": 0x4649,
    "106K250": 0xF459,
    "107K250": 0x359D,
}

EXPECTED_C = {
    "077K250": 0x7993,
    "078K250": 0x7B16,
    "079K250": 0xAF56,
    "080K250": 0x8F87,
    "081K250": 0xB6B0,
    "082K250": 0x76BC,
    "083K250": 0xD13D,  # Source dump is uniform 0xFB blank/unprogrammed
    "084K250": 0xAA79,
    "085K250": 0x6AEE,
    "086K250": 0xF5BB,
}

C_SOLVER_SRC = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <limits.h>

static inline int s8(uint8_t b) { return (int)(int8_t)b; }
static inline double robust_nis(double e, double invS) {
    /* Student-t (nu=3) negative log-likelihood: 2.0 * log(1 + e^2 * invS / 3.0) */
    double z2 = e * e * invS;
    return 2.0 * log(1.0 + z2 * 0.333333333333);
}

/* Single-channel exact-count 1D Viterbi using 2nd-difference + slope continuity + anti-clump prior.
 * Writes `M` reconstructed bytes to `out` and `k_path[0..M]` cumulative inserts. */
static void solve_single(
    const uint8_t *raw, int N, int K,
    uint8_t *out, int *k_path,
    const uint8_t *forbid /* length N+K or NULL */
) {
    int M = N + K;
    if (K == 0) {
        memcpy(out, raw, M);
        for (int t = 0; t <= M; t++) k_path[t] = 0;
        return;
    }
    if (N == 0) {
        memset(out, 0x0D, M);
        for (int t = 0; t <= M; t++) k_path[t] = t;
        return;
    }

    int W = K + 1;
    size_t row_sz = (size_t)W * 4;
    int64_t *dp_prev = (int64_t *)malloc(row_sz * sizeof(int64_t));
    int64_t *dp_curr = (int64_t *)malloc(row_sz * sizeof(int64_t));
    uint8_t *bp = (uint8_t *)malloc((size_t)M * row_sz);

    const int64_t INF = (int64_t)1e15;
    for (size_t i = 0; i < row_sz; i++) dp_prev[i] = INF;
    dp_prev[0] = 0; /* k=0, hist=0 */

    for (int t = 0; t < M; t++) {
        for (size_t i = 0; i < row_sz; i++) dp_curr[i] = INF;
        int pk_min = (t > N) ? (t - N) : 0;
        int pk_max = (t < K) ? t : K;

        for (int pk = pk_min; pk <= pk_max; pk++) {
            int pi = t - pk;
            for (int h = 0; h < 4; h++) {
                int64_t base = dp_prev[pk * 4 + h];
                if (base >= INF) continue;

                int s_m1 = (t >= 1) ? ((h & 1) ? 13 : s8(raw[pi - 1])) : 0;
                int prev_pi2 = pi - 1 - ((h & 1) ? 0 : 1);
                int s_m2 = (t >= 2) ? ((h & 2) ? 13 : s8(raw[prev_pi2])) : s_m1;

                /* Option 0: emit raw[pi] */
                if (pi < N) {
                    int s0 = s8(raw[pi]);
                    int64_t cost = 0;
                    if (t >= 2) {
                        int d2 = s_m2 - 2 * s_m1 + s0;
                        int d1 = s0 - s_m1;
                        cost = (int64_t)d2 * d2 * 16 + (int64_t)d1 * d1;
                    } else if (t == 1) {
                        int d1 = s0 - s_m1;
                        cost = (int64_t)d1 * d1 * 2;
                    }
                    int nh = ((h & 1) << 1) | 0;
                    size_t idx = (size_t)pk * 4 + nh;
                    if (base + cost < dp_curr[idx]) {
                        dp_curr[idx] = base + cost;
                        bp[(size_t)t * row_sz + idx] = (uint8_t)h;
                    }
                }
                /* Option 1: insert 0x0D */
                if (pk < K && (!forbid || !forbid[t])) {
                    int s0 = 13;
                    int64_t cost = 0;
                    if (t >= 2) {
                        int d2 = s_m2 - 2 * s_m1 + s0;
                        int d1 = s0 - s_m1;
                        cost = (int64_t)d2 * d2 * 16 + (int64_t)d1 * d1;
                    } else if (t == 1) {
                        int d1 = s0 - s_m1;
                        cost = (int64_t)d1 * d1 * 2;
                    }
                    if (h & 1) cost += ((h & 2) ? 500000 : 2000);
                    int nh = ((h & 1) << 1) | 1;
                    size_t idx = (size_t)(pk + 1) * 4 + nh;
                    if (base + cost < dp_curr[idx]) {
                        dp_curr[idx] = base + cost;
                        bp[(size_t)t * row_sz + idx] = (uint8_t)(4 + h);
                    }
                }
            }
        }
        int64_t *tmp = dp_prev; dp_prev = dp_curr; dp_curr = tmp;
    }

    int best_h = 0;
    int64_t best_cost = INF;
    for (int h = 0; h < 4; h++) {
        if (dp_prev[K * 4 + h] < best_cost) {
            best_cost = dp_prev[K * 4 + h];
            best_h = h;
        }
    }
    if (best_cost >= INF) {
        fprintf(stderr, "solve_single failed! N=%d K=%d\n", N, K);
        exit(2);
    }

    int ck = K, ch = best_h;
    k_path[M] = ck;
    for (int t = M - 1; t >= 0; t--) {
        uint8_t code = bp[(size_t)t * row_sz + (size_t)ck * 4 + ch];
        int ins_here = (code >> 2) & 1;
        ch = code & 3;
        if (ins_here) {
            ck--;
            out[t] = 0x0D;
        } else {
            out[t] = raw[t - ck];
        }
        k_path[t] = ck;
    }
    free(dp_prev); free(dp_curr); free(bp);
}

/* Regularized Random-Walk Forward-Backward Kalman AR(4) Smoother on interleaved y[0..2M-1]. */
static void kalman_ar4_smooth(const uint8_t *oe, const uint8_t *oo, int M, double (*w_out)[4], double *invS_out) {
    int N2 = 2 * M;
    double *y = (double *)malloc((size_t)N2 * sizeof(double));
    for (int i = 0; i < M; i++) {
        y[2 * i]     = (double)s8(oe[i]);
        y[2 * i + 1] = (double)s8(oo[i]);
    }
    double (*wf)[4] = malloc((size_t)N2 * sizeof(*wf));
    double *Sf = (double *)malloc((size_t)N2 * sizeof(double));
    double w[4] = {1.6, -0.75, 0.15, 0.0};
    double P[4][4];
    memset(P, 0, sizeof(P));
    for (int i = 0; i < 4; i++) P[i][i] = 0.05;
    const double qw = 1e-5, Rm = 6.0;

    for (int t = 0; t < N2; t++) {
        for (int k = 0; k < 4; k++) wf[t][k] = w[k];
        if (t < 4) { Sf[t] = 16.0; continue; }
        double phi[4] = {y[t-1], y[t-2], y[t-3], y[t-4]};
        double e = y[t] - (w[0]*phi[0] + w[1]*phi[1] + w[2]*phi[2] + w[3]*phi[3]);
        for (int i = 0; i < 4; i++) { P[i][i] += qw; if (P[i][i] > 0.1) P[i][i] = 0.1; }
        double Pphi[4] = {0, 0, 0, 0};
        for (int i = 0; i < 4; i++)
            for (int j = 0; j < 4; j++) Pphi[i] += P[i][j] * phi[j];
        double S = Rm + phi[0]*Pphi[0] + phi[1]*Pphi[1] + phi[2]*Pphi[2] + phi[3]*Pphi[3];
        Sf[t] = S;
        double ec = e > 20.0 ? 20.0 : (e < -20.0 ? -20.0 : e);
        for (int i = 0; i < 4; i++) {
            double Ki = Pphi[i] / S;
            w[i] += Ki * ec;
            if (w[i] > 2.4) w[i] = 2.4;
            if (w[i] < -2.4) w[i] = -2.4;
            for (int j = 0; j < 4; j++) P[i][j] -= Ki * Pphi[j];
        }
    }

    double wb[4] = {1.6, -0.75, 0.15, 0.0};
    memset(P, 0, sizeof(P));
    for (int i = 0; i < 4; i++) P[i][i] = 0.05;
    for (int t = N2 - 1; t >= 0; t--) {
        if (t <= N2 - 5 && t >= 4) {
            for (int k = 0; k < 4; k++) w_out[t][k] = 0.5 * (wf[t][k] + wb[k]);
        } else {
            for (int k = 0; k < 4; k++) w_out[t][k] = wf[t][k];
        }
        double S = Sf[t];
        if (S < 4.0) S = 4.0;
        if (S > 64.0) S = 64.0;
        invS_out[t] = 1.0 / S;
        if (t <= N2 - 5) {
            double phi[4] = {y[t+1], y[t+2], y[t+3], y[t+4]};
            double e = y[t] - (wb[0]*phi[0] + wb[1]*phi[1] + wb[2]*phi[2] + wb[3]*phi[3]);
            double ec = e > 20.0 ? 20.0 : (e < -20.0 ? -20.0 : e);
            for (int i = 0; i < 4; i++) { P[i][i] += qw; if (P[i][i] > 0.1) P[i][i] = 0.1; }
            double Pphi[4] = {0, 0, 0, 0};
            for (int i = 0; i < 4; i++)
                for (int j = 0; j < 4; j++) Pphi[i] += P[i][j] * phi[j];
            double Sb = Rm + phi[0]*Pphi[0] + phi[1]*Pphi[1] + phi[2]*Pphi[2] + phi[3]*Pphi[3];
            for (int i = 0; i < 4; i++) {
                double Ki = Pphi[i] / Sb;
                wb[i] += Ki * ec;
                if (wb[i] > 2.4) wb[i] = 2.4;
                if (wb[i] < -2.4) wb[i] = -2.4;
                for (int j = 0; j < 4; j++) P[i][j] -= Ki * Pphi[j];
            }
        }
    }
    free(y); free(wf); free(Sf);
}

/* Unbanded Relative-Lag (u, d) Viterbi DP with Student-t Robust Kalman AR(4) innovation cost.
 * Uses a 3-row rolling DP ring buffer (~20 KB L1 cache) and preallocated workspace buffers. */
static int run_lag_dp(
    const uint8_t *re, int Ne,
    const uint8_t *ro, int No,
    double ins_pen, int dmax,
    const double (*w_u)[4],
    const double *invS_u,
    double *dp_ring, uint8_t *bp, uint8_t *te, uint8_t *to,
    uint8_t *out_e, uint8_t *out_o,
    int *out_M, int *out_ke, int *out_ko
) {
    int U = Ne + No;
    int nd = 2 * dmax + 1;
    const size_t STRIDE = (size_t)nd * 16;
    const double INF = 1e18;
    for (size_t i = 0; i < 3 * STRIDE; i++) dp_ring[i] = INF;

    dp_ring[(size_t)dmax * 16 + 0] = 0.0;
    int d_end = No - Ne;

    for (int u = 0; u < U; u++) {
        double we[4] = {1.65, -0.80, 0.15, 0.0};
        double wo[4] = {1.65, -0.80, 0.15, 0.0};
        double ise = 1.0 / 9.0, iso = 1.0 / 9.0;
        if (w_u) {
            for (int k = 0; k < 4; k++) { we[k] = w_u[u][k]; wo[k] = w_u[u][k]; }
            ise = iso = invS_u[u];
        }
        double *row0 = dp_ring + (size_t)(u % 3) * STRIDE;
        double *row1 = dp_ring + (size_t)((u + 1) % 3) * STRIDE;
        double *row2 = dp_ring + (size_t)((u + 2) % 3) * STRIDE;
        uint8_t *bp1 = bp + (size_t)(u + 1) * STRIDE;
        uint8_t *bp2 = bp + (size_t)(u + 2) * STRIDE;

        int max_reach = (u < dmax) ? u : dmax;
        int rem_u = U - u;
        int d_lo = -max_reach;
        if (d_end - rem_u > d_lo) d_lo = d_end - rem_u;
        int d_hi = max_reach;
        if (d_end + rem_u < d_hi) d_hi = d_end + rem_u;
        if (d_lo < -dmax) d_lo = -dmax;
        if (d_hi > dmax) d_hi = dmax;

        for (int d = d_lo + ((u + d_lo) & 1); d <= d_hi; d += 2) {
            int di = d + dmax;
            int ie = (u - d) >> 1;
            int io = (u + d) >> 1;
            if (ie < 0 || ie > Ne || io < 0 || io > No) continue;

            for (int h = 0; h < 16; h++) {
                double base = row0[(size_t)di * 16 + h];
                if (base >= INF) continue;

                int p1e = (h >> 1) & 1, p1o = h & 1;
                int p2e = (h >> 3) & 1, p2o = (h >> 2) & 1;
                int has1 = (u > 0), has2 = (u >= 2);
                double e_m1 = has1 ? (p1e ? 13.0 : (ie > 0 ? (double)s8(re[ie - 1]) : 0.0)) : 0.0;
                double o_m1 = has1 ? (p1o ? 13.0 : (io > 0 ? (double)s8(ro[io - 1]) : 0.0)) : 0.0;
                int ie2 = ie - 1 - (p1e ? 0 : 1);
                int io2 = io - 1 - (p1o ? 0 : 1);
                double e_m2 = (has2 && (p2e || ie2 >= 0)) ? (p2e ? 13.0 : (double)s8(re[ie2])) : e_m1;
                double o_m2 = (has2 && (p2o || io2 >= 0)) ? (p2o ? 13.0 : (double)s8(ro[io2])) : o_m1;

                double pred_e = we[0]*o_m1 + we[1]*e_m1 + we[2]*o_m2 + we[3]*e_m2;

                /* act=0: emit (re[ie], ro[io]) */
                if (ie < Ne && io < No) {
                    double e1 = (double)s8(re[ie]);
                    double o1 = (double)s8(ro[io]);
                    double ee = has2 ? (e1 - pred_e) : 0.0;
                    double pred_o = wo[0]*e1 + wo[1]*o_m1 + wo[2]*e_m1 + wo[3]*o_m2;
                    double eo = has2 ? (o1 - pred_o) : 0.0;
                    double cost = robust_nis(ee, ise) + robust_nis(eo, iso);
                    int nh = ((h & 3) << 2) | 0;
                    size_t c_idx = (size_t)di * 16 + nh;
                    if (base + cost < row2[c_idx]) {
                        row2[c_idx] = base + cost;
                        bp2[c_idx] = (uint8_t)((0 << 4) | h);
                    }
                }
                /* act=1: emit (0x0D, ro[io]) */
                if (io < No && di + 1 < nd) {
                    double e1 = 13.0;
                    double o1 = (double)s8(ro[io]);
                    double ee = has2 ? (e1 - pred_e) : 0.0;
                    double pred_o = wo[0]*e1 + wo[1]*o_m1 + wo[2]*e_m1 + wo[3]*o_m2;
                    double eo = has2 ? (o1 - pred_o) : 0.0;
                    double cost = robust_nis(ee, ise) + robust_nis(eo, iso) + ins_pen;
                    if (p1e) cost += (p2e ? 50.0 : 2.5);
                    int nh = ((h & 3) << 2) | 2;
                    size_t c_idx = (size_t)(di + 1) * 16 + nh;
                    if (base + cost < row1[c_idx]) {
                        row1[c_idx] = base + cost;
                        bp1[c_idx] = (uint8_t)((1 << 4) | h);
                    }
                }
                /* act=2: emit (re[ie], 0x0D) */
                if (ie < Ne && di - 1 >= 0) {
                    double e1 = (double)s8(re[ie]);
                    double o1 = 13.0;
                    double ee = has2 ? (e1 - pred_e) : 0.0;
                    double pred_o = wo[0]*e1 + wo[1]*o_m1 + wo[2]*e_m1 + wo[3]*o_m2;
                    double eo = has2 ? (o1 - pred_o) : 0.0;
                    double cost = robust_nis(ee, ise) + robust_nis(eo, iso) + ins_pen;
                    if (p1o) cost += (p2o ? 50.0 : 2.5);
                    int nh = ((h & 3) << 2) | 1;
                    size_t c_idx = (size_t)(di - 1) * 16 + nh;
                    if (base + cost < row1[c_idx]) {
                        row1[c_idx] = base + cost;
                        bp1[c_idx] = (uint8_t)((2 << 4) | h);
                    }
                }
            }
        }
        /* Clear row0 for reuse at u+3 */
        for (size_t i = 0; i < STRIDE; i++) row0[i] = INF;
    }

    int di_end = d_end + dmax;
    if (di_end < 0 || di_end >= nd) return -1;
    double *rowU = dp_ring + (size_t)(U % 3) * STRIDE;
    double best = INF; int bh = 0;
    for (int h = 0; h < 16; h++) {
        double v = rowU[(size_t)di_end * 16 + h];
        if (v < best) { best = v; bh = h; }
    }
    if (best >= INF) return -2;

    int len = 0, ke = 0, ko = 0;
    int cu = U, cdi = di_end, ch = bh;
    while (cu > 0) {
        uint8_t code = bp[(size_t)cu * STRIDE + (size_t)cdi * 16 + ch];
        int act = code >> 4;
        int ph = code & 15;
        int cd = cdi - dmax;
        int ie = (cu - cd) >> 1;
        int io = (cu + cd) >> 1;
        if (act == 0) {
            te[len] = re[ie - 1]; to[len] = ro[io - 1]; len++;
            cu -= 2;
        } else if (act == 1) {
            te[len] = 0x0D; to[len] = ro[io - 1]; len++; ke++;
            cu -= 1; cdi -= 1;
        } else {
            te[len] = re[ie - 1]; to[len] = 0x0D; len++; ko++;
            cu -= 1; cdi += 1;
        }
        ch = ph;
    }
    for (int i = 0; i < len; i++) {
        out_e[i] = te[len - 1 - i];
        out_o[i] = to[len - 1 - i];
    }
    *out_M = len; *out_ke = ke; *out_ko = ko;
    return 0;
}

/* Coupled even/odd Kalman AR(4) Viterbi solver */
static void solve_coupled(
    const uint8_t *re, int Ne, int Ke,
    const uint8_t *ro, int No, int Ko,
    uint8_t *out_e, uint8_t *out_o
) {
    int M_target = Ne + Ke;
    if (No + Ko != M_target) {
        fprintf(stderr, "Length mismatch: %d+%d != %d+%d\n", Ne, Ke, No, Ko);
        exit(3);
    }
    if (Ke == 0 && Ko == 0) {
        memcpy(out_e, re, (size_t)M_target);
        memcpy(out_o, ro, (size_t)M_target);
        return;
    }
    if (Ke == 0 || Ko == 0) {
        const double INF = 1e18;
        int K = (Ke > 0) ? Ke : Ko;
        const uint8_t *r_var = (Ke > 0) ? re : ro;
        const uint8_t *r_fix = (Ke > 0) ? ro : re;
        int var_is_even = (Ke > 0);
        int W = K + 1;
        double *dp_p = (double *)malloc((size_t)W * 2 * sizeof(double));
        double *dp_c = (double *)malloc((size_t)W * 2 * sizeof(double));
        uint8_t *bp = (uint8_t *)malloc((size_t)M_target * (size_t)W * 2);
        for (int i = 0; i < W * 2; i++) dp_p[i] = INF;
        dp_p[0] = 0.0;
        const double invS = 1.0 / 9.0;
        for (int t = 0; t < M_target; t++) {
            for (int i = 0; i < W * 2; i++) dp_c[i] = INF;
            double fix_curr = (double)s8(r_fix[t]);
            double fix_prev = (t > 0) ? (double)s8(r_fix[t - 1]) : fix_curr;
            for (int k = 0; k <= K; k++) {
                int pi = t - k;
                for (int h = 0; h < 2; h++) {
                    double base = dp_p[k * 2 + h];
                    if (base >= INF) continue;
                    double var_prev = (t > 0) ? (h ? 13.0 : (double)s8(r_var[pi - 1])) : 0.0;
                    for (int ins = 0; ins <= 1; ins++) {
                        if (ins == 0 && pi >= M_target - K) continue;
                        if (ins == 1 && k >= K) continue;
                        double var_curr = ins ? 13.0 : (double)s8(r_var[pi]);
                        double cost = 0.0;
                        if (t > 0) {
                            double e0 = var_is_even ? var_prev : fix_prev;
                            double o0 = var_is_even ? fix_prev : var_prev;
                            double e1 = var_is_even ? var_curr : fix_curr;
                            double o1 = var_is_even ? fix_curr : var_curr;
                            double c1 = e0 - 2.0 * o0 + e1;
                            double c2 = o0 - 2.0 * e1 + o1;
                            cost = robust_nis(c1, invS) + robust_nis(c2, invS);
                        }
                        if (ins && h) cost += 2.5;
                        int nk = k + ins;
                        size_t idx = (size_t)nk * 2 + (size_t)ins;
                        if (base + cost < dp_c[idx]) {
                            dp_c[idx] = base + cost;
                            bp[(size_t)t * (size_t)W * 2 + idx] = (uint8_t)((ins << 1) | h);
                        }
                    }
                }
            }
            double *tmp = dp_p; dp_p = dp_c; dp_c = tmp;
        }
        int bh = (dp_p[K * 2 + 1] < dp_p[K * 2 + 0]) ? 1 : 0;
        int ck = K, ch = bh;
        uint8_t *v_out = var_is_even ? out_e : out_o;
        uint8_t *f_out = var_is_even ? out_o : out_e;
        memcpy(f_out, r_fix, (size_t)M_target);
        for (int t = M_target - 1; t >= 0; t--) {
            uint8_t c = bp[(size_t)t * (size_t)W * 2 + (size_t)ck * 2 + ch];
            int ins = (c >> 1) & 1;
            ch = c & 1;
            if (ins) { ck--; v_out[t] = 0x0D; }
            else { v_out[t] = r_var[t - ck]; }
        }
        free(dp_p); free(dp_c); free(bp);
        return;
    }

    int dmax = abs(No - Ne) + 48;
    if (dmax < 80) dmax = 80;
    int U = Ne + No;
    int nd = 2 * dmax + 1;
    const size_t STRIDE = (size_t)nd * 16;
    double *dp_ring = (double *)malloc(3 * STRIDE * sizeof(double));
    uint8_t *bp = (uint8_t *)malloc((size_t)(U + 3) * STRIDE);
    uint8_t *te = (uint8_t *)malloc((size_t)U + (size_t)Ke + 256);
    uint8_t *to = (uint8_t *)malloc((size_t)U + (size_t)Ke + 256);
    uint8_t *oe = (uint8_t *)malloc((size_t)U + (size_t)Ke + 256);
    uint8_t *oo = (uint8_t *)malloc((size_t)U + (size_t)Ko + 256);
    int M = 0, ke = 0, ko = 0;

    /* Pass 0: static AR(4) binary search for ins_pen so ke <= Ke */
    double lo = -5.0, hi = 30.0;
    int rc = run_lag_dp(re, Ne, ro, No, hi, dmax, NULL, NULL, dp_ring, bp, te, to, oe, oo, &M, &ke, &ko);
    if (rc != 0) { fprintf(stderr, "run_lag_dp failed rc=%d\n", rc); exit(5); }
    if (ke > Ke) hi = 200.0;
    for (int it = 0; it < 14; it++) {
        double mid = 0.5 * (lo + hi);
        run_lag_dp(re, Ne, ro, No, mid, dmax, NULL, NULL, dp_ring, bp, te, to, oe, oo, &M, &ke, &ko);
        if (ke == Ke) { hi = mid; break; }
        if (ke > Ke) lo = mid; else hi = mid;
    }
    if (ke > Ke) {
        run_lag_dp(re, Ne, ro, No, hi, dmax, NULL, NULL, dp_ring, bp, te, to, oe, oo, &M, &ke, &ko);
    }

    /* Passes 1..2: Kalman AR(4) refined */
    double (*w_u)[4] = malloc((size_t)(U + 4) * sizeof(*w_u));
    double *invS_u = (double *)malloc((size_t)(U + 4) * sizeof(double));
    for (int pass = 1; pass <= 2; pass++) {
        double (*w_m)[4] = malloc((size_t)(2 * M) * sizeof(*w_m));
        double *is_m = (double *)malloc((size_t)(2 * M) * sizeof(double));
        kalman_ar4_smooth(oe, oo, M, w_m, is_m);
        int u_cur = 0;
        for (int i = 0; i < M; i++) {
            int adv = (oe[i] != 0x0D ? 1 : 0) + (oo[i] != 0x0D ? 1 : 0);
            for (int a = 0; a < adv && u_cur <= U; a++, u_cur++) {
                for (int k = 0; k < 4; k++) w_u[u_cur][k] = 0.5 * (w_m[2*i][k] + w_m[2*i + 1][k]);
                invS_u[u_cur] = 0.5 * (is_m[2*i] + is_m[2*i + 1]);
            }
        }
        while (u_cur <= U) {
            for (int k = 0; k < 4; k++) w_u[u_cur][k] = 1.6*(k==0) - 0.75*(k==1);
            invS_u[u_cur] = 1.0 / 9.0;
            u_cur++;
        }
        free(w_m); free(is_m);

        double plo = -5.0, phi = 30.0;
        run_lag_dp(re, Ne, ro, No, phi, dmax, w_u, invS_u, dp_ring, bp, te, to, oe, oo, &M, &ke, &ko);
        if (ke > Ke) phi = 200.0;
        for (int it = 0; it < 14; it++) {
            double mid = 0.5 * (plo + phi);
            run_lag_dp(re, Ne, ro, No, mid, dmax, w_u, invS_u, dp_ring, bp, te, to, oe, oo, &M, &ke, &ko);
            if (ke == Ke) { phi = mid; break; }
            if (ke > Ke) plo = mid; else phi = mid;
        }
        if (ke > Ke) {
            run_lag_dp(re, Ne, ro, No, phi, dmax, w_u, invS_u, dp_ring, bp, te, to, oe, oo, &M, &ke, &ko);
        }
    }

    /* Stage 2: Insert remaining (Ke - ke) simultaneous (0x0D, 0x0D) pairs */
    int rem = Ke - ke;
    if (rem > 0) {
        double (*w_m)[4] = malloc((size_t)(2 * M) * sizeof(*w_m));
        double *is_m = (double *)malloc((size_t)(2 * M) * sizeof(double));
        kalman_ar4_smooth(oe, oo, M, w_m, is_m);
        typedef struct { double score; int pos; } Cand;
        Cand *cands = (Cand *)malloc((size_t)(M + 2) * sizeof(Cand));
        int nc = 0;
        for (int i = 1; i < M; i++) {
            double e_m1 = (double)s8(oe[i-1]), o_m1 = (double)s8(oo[i-1]);
            double e_m2 = (i >= 2) ? (double)s8(oe[i-2]) : e_m1;
            double o_m2 = (i >= 2) ? (double)s8(oo[i-2]) : o_m1;
            double e_p0 = (double)s8(oe[i]), o_p0 = (double)s8(oo[i]);
            double *w = w_m[2*i];
            double isv = is_m[2*i];
            double pred_e0 = w[0]*o_m1 + w[1]*e_m1 + w[2]*o_m2 + w[3]*e_m2;
            double pred_o0 = w[0]*e_p0 + w[1]*o_m1 + w[2]*e_m1 + w[3]*o_m2;
            double cost_no = robust_nis(e_p0 - pred_e0, isv) + robust_nis(o_p0 - pred_o0, isv);
            double pe1 = pred_e0;
            double po1 = w[0]*13.0 + w[1]*o_m1 + w[2]*e_m1 + w[3]*o_m2;
            double pe2 = w[0]*13.0 + w[1]*13.0 + w[2]*o_m1 + w[3]*e_m1;
            double po2 = w[0]*e_p0 + w[1]*13.0 + w[2]*13.0 + w[3]*o_m1;
            double cost_yes = robust_nis(13.0 - pe1, isv) + robust_nis(13.0 - po1, isv)
                            + robust_nis(e_p0 - pe2, isv) + robust_nis(o_p0 - po2, isv);
            if (oe[i-1] == 0x0D || oo[i-1] == 0x0D || oe[i] == 0x0D || oo[i] == 0x0D) cost_yes += 20.0;
            cands[nc].score = cost_yes - cost_no;
            cands[nc].pos = i;
            nc++;
        }
        int *ins_cnt = (int *)calloc((size_t)(M + 1), sizeof(int));
        for (int r = 0; r < rem; r++) {
            double best_s = 1e18; int best_p = -1;
            for (int c = 0; c < nc; c++) {
                int p = cands[c].pos;
                if (ins_cnt[p] || (p > 0 && ins_cnt[p-1]) || (p + 1 <= M && ins_cnt[p+1])) continue;
                if (cands[c].score < best_s) { best_s = cands[c].score; best_p = p; }
            }
            if (best_p < 0) {
                for (int c = 0; c < nc; c++) {
                    if (cands[c].score < best_s) { best_s = cands[c].score; best_p = cands[c].pos; }
                }
                if (best_p < 0) best_p = M / 2;
            }
            ins_cnt[best_p]++;
        }
        int out_i = 0;
        for (int i = 0; i < M; i++) {
            for (int c = 0; c < ins_cnt[i]; c++) {
                out_e[out_i] = 0x0D; out_o[out_i] = 0x0D; out_i++;
            }
            out_e[out_i] = oe[i]; out_o[out_i] = oo[i]; out_i++;
        }
        ke += rem; ko += rem; M = out_i;
        free(ins_cnt); free(cands); free(w_m); free(is_m);
    } else {
        memcpy(out_e, oe, (size_t)M);
        memcpy(out_o, oo, (size_t)M);
    }

    if (ke != Ke || ko != Ko || M != M_target) {
        fprintf(stderr, "solve_coupled mismatch: M=%d/%d ke=%d/%d ko=%d/%d\n",
                M, M_target, ke, Ke, ko, Ko);
        exit(6);
    }
    free(dp_ring); free(bp); free(te); free(to);
    free(w_u); free(invS_u); free(oe); free(oo);
}

/* 4-channel LSB Viterbi anchored against reconstructed MSB chips e0,o0,e1,o1. */
static void solve_lsb(
    const uint8_t *raw, int N, int K,
    const uint8_t *e0, const uint8_t *o0,
    const uint8_t *e1, const uint8_t *o1,
    const uint8_t *forbid, /* length M */
    int has_q0, int has_q1,
    uint8_t *out
) {
    int M = N + K;
    if (K == 0) { memcpy(out, raw, M); return; }
    int W = K + 1;
    size_t row_sz = (size_t)W * 2;
    int64_t *dp_p = (int64_t *)malloc(row_sz * sizeof(int64_t));
    int64_t *dp_c = (int64_t *)malloc(row_sz * sizeof(int64_t));
    uint8_t *bp = (uint8_t *)malloc((size_t)M * row_sz);
    const int64_t INF = (int64_t)1e15;

    for (size_t i = 0; i < row_sz; i++) dp_p[i] = INF;
    dp_p[0] = 0;

    for (int t = 0; t < M; t++) {
        for (size_t i = 0; i < row_sz; i++) dp_c[i] = INF;
        int q0_zero = (!has_q0 || (e0[t] == 0 && o0[t] == 0));
        int q1_zero = (!has_q1 || (e1[t] == 0 && o1[t] == 0));
        /* Check smoothness on Q0 and Q1 across (e_prev, o_prev, e_curr, o_curr) 10-bit samples */
        int pk_min = (t > N) ? (t - N) : 0;
        int pk_max = (t < K) ? t : K;

        for (int pk = pk_min; pk <= pk_max; pk++) {
            int pi = t - pk;
            for (int h = 0; h < 2; h++) {
                int64_t base = dp_p[pk * 2 + h];
                if (base >= INF) continue;
                uint8_t prev_b = (t > 0) ? (h ? 0x0D : raw[pi - 1]) : 0;

                for (int ins = 0; ins <= 1; ins++) {
                    if (ins == 0 && pi >= N) continue;
                    if (ins == 1 && (pk >= K || (forbid && forbid[t]))) continue;
                    uint8_t cur_b = ins ? 0x0D : raw[pi];

                    int64_t cost = 0;
                    if (q0_zero && (cur_b & 0x0F) != 0) cost += 2000;
                    if (q1_zero && (cur_b & 0xF0) != 0) cost += 2000;
                    if (ins == 1 && h == 1) cost += 25000;
                    if (ins == 1 && q1_zero && !has_q0) cost += 500;
                    /* Penalize misaligned nibble if both channels inactive */
                    if (t > 0) {
                        if (has_q0 && (e0[t] != 0 || o0[t] != 0)) {
                            int lsb_pe = ((prev_b >> 0) & 1) * 2 + ((prev_b >> 1) & 1);
                            int lsb_po = ((prev_b >> 2) & 1) * 2 + ((prev_b >> 3) & 1);
                            int lsb_ce = ((cur_b >> 0) & 1) * 2 + ((cur_b >> 1) & 1);
                            int lsb_co = ((cur_b >> 2) & 1) * 2 + ((cur_b >> 3) & 1);
                            int se0 = s8(e0[t-1]) * 4 + lsb_pe;
                            int so0 = s8(o0[t-1]) * 4 + lsb_po;
                            int se1 = s8(e0[t])   * 4 + lsb_ce;
                            int so1 = s8(o0[t])   * 4 + lsb_co;
                            int d1 = se0 - 2 * so0 + se1;
                            int d2 = so0 - 2 * se1 + so1;
                            cost += (int64_t)d1 * d1 + (int64_t)d2 * d2;
                        }
                        if (has_q1 && (e1[t] != 0 || o1[t] != 0)) {
                            int lsb_pe = ((prev_b >> 4) & 1) * 2 + ((prev_b >> 5) & 1);
                            int lsb_po = ((prev_b >> 6) & 1) * 2 + ((prev_b >> 7) & 1);
                            int lsb_ce = ((cur_b >> 4) & 1) * 2 + ((cur_b >> 5) & 1);
                            int lsb_co = ((cur_b >> 6) & 1) * 2 + ((cur_b >> 7) & 1);
                            int se0 = s8(e1[t-1]) * 4 + lsb_pe;
                            int so0 = s8(o1[t-1]) * 4 + lsb_po;
                            int se1 = s8(e1[t])   * 4 + lsb_ce;
                            int so1 = s8(o1[t])   * 4 + lsb_co;
                            int d1 = se0 - 2 * so0 + se1;
                            int d2 = so0 - 2 * se1 + so1;
                            cost += (int64_t)d1 * d1 + (int64_t)d2 * d2;
                        }
                    }

                    int nk = pk + ins;
                    size_t idx = (size_t)nk * 2 + ins;
                    if (base + cost < dp_c[idx]) {
                        dp_c[idx] = base + cost;
                        bp[(size_t)t * row_sz + idx] = (uint8_t)((ins << 1) | h);
                    }
                }
            }
        }
        int64_t *tmp = dp_p; dp_p = dp_c; dp_c = tmp;
    }

    int bh = (dp_p[K * 2 + 1] < dp_p[K * 2 + 0]) ? 1 : 0;
    if (dp_p[K * 2 + bh] >= INF) {
        fprintf(stderr, "solve_lsb failed! N=%d K=%d\n", N, K);
        exit(4);
    }
    int ck = K, ch = bh;
    for (int t = M - 1; t >= 0; t--) {
        uint8_t c = bp[(size_t)t * row_sz + (size_t)ck * 2 + ch];
        int ins = (c >> 1) & 1;
        ch = c & 1;
        if (ins) { ck--; out[t] = 0x0D; }
        else { out[t] = raw[t - ck]; }
    }
    free(dp_p); free(dp_c); free(bp);
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    const char *mode = argv[1];
    if (strcmp(mode, "coupled") == 0) {
        /* coupled <re_bin> <ro_bin> <Ke> <Ko> <oe_bin> <oo_bin> */
        int Ke = atoi(argv[4]), Ko = atoi(argv[5]);
        FILE *fe = fopen(argv[2], "rb"), *fo = fopen(argv[3], "rb");
        fseek(fe, 0, SEEK_END); int Ne = (int)ftell(fe); rewind(fe);
        fseek(fo, 0, SEEK_END); int No = (int)ftell(fo); rewind(fo);
        uint8_t *re = malloc(Ne + 1), *ro = malloc(No + 1);
        fread(re, 1, Ne, fe); fread(ro, 1, No, fo);
        fclose(fe); fclose(fo);
        int M = Ne + Ke;
        uint8_t *oe = malloc(M), *oo = malloc(M);
        solve_coupled(re, Ne, Ke, ro, No, Ko, oe, oo);
        FILE *fwe = fopen(argv[6], "wb"), *fwo = fopen(argv[7], "wb");
        fwrite(oe, 1, M, fwe); fwrite(oo, 1, M, fwo);
        fclose(fwe); fclose(fwo);
        free(re); free(ro); free(oe); free(oo);
    } else if (strcmp(mode, "single") == 0) {
        /* single <r_bin> <K> <o_bin> [<forbid_bin>] */
        int K = atoi(argv[3]);
        FILE *f = fopen(argv[2], "rb");
        fseek(f, 0, SEEK_END); int N = (int)ftell(f); rewind(f);
        uint8_t *r = malloc(N + 1);
        fread(r, 1, N, f); fclose(f);
        int M = N + K;
        uint8_t *forbid = NULL;
        if (argc >= 6) {
            forbid = malloc(M);
            FILE *ff = fopen(argv[5], "rb");
            fread(forbid, 1, M, ff); fclose(ff);
        }
        uint8_t *o = malloc(M);
        int *kp = malloc((M + 1) * sizeof(int));
        solve_single(r, N, K, o, kp, forbid);
        FILE *fw = fopen(argv[4], "wb");
        fwrite(o, 1, M, fw); fclose(fw);
        free(r); free(o); free(kp); if (forbid) free(forbid);
    } else if (strcmp(mode, "lsb") == 0) {
        /* lsb <raw_bin> <K> <e0> <o0> <e1> <o1> <forbid_bin> <has_q0> <has_q1> <out_bin> */
        int K = atoi(argv[3]);
        int has_q0 = atoi(argv[9]), has_q1 = atoi(argv[10]);
        FILE *f = fopen(argv[2], "rb");
        fseek(f, 0, SEEK_END); int N = (int)ftell(f); rewind(f);
        int M = N + K;
        uint8_t *r = malloc(N + 1); fread(r, 1, N, f); fclose(f);
        uint8_t *e0 = malloc(M), *o0 = malloc(M), *e1 = malloc(M), *o1 = malloc(M), *fb = malloc(M);
        FILE *f0 = fopen(argv[4], "rb"); fread(e0, 1, M, f0); fclose(f0);
        FILE *f1 = fopen(argv[5], "rb"); fread(o0, 1, M, f1); fclose(f1);
        FILE *f2 = fopen(argv[6], "rb"); fread(e1, 1, M, f2); fclose(f2);
        FILE *f3 = fopen(argv[7], "rb"); fread(o1, 1, M, f3); fclose(f3);
        FILE *f4 = fopen(argv[8], "rb"); fread(fb, 1, M, f4); fclose(f4);
        uint8_t *o = malloc(M);
        solve_lsb(r, N, K, e0, o0, e1, o1, fb, has_q0, has_q1, o);
        FILE *fw = fopen(argv[11], "wb"); fwrite(o, 1, M, fw); fclose(fw);
        free(r); free(e0); free(o0); free(e1); free(o1); free(fb); free(o);
    }
    return 0;
}
"""

SOLVER_BIN = os.path.join(tempfile.gettempdir(), "k250_viterbi_rom")


def compile_solver(cc: str = "cc"):
    c_path = os.path.join(tempfile.gettempdir(), "k250_viterbi_rom.c")
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(C_SOLVER_SRC)
    subprocess.run([cc, "-O3", "-Wall", c_path, "-o", SOLVER_BIN, "-lm"], check=True)


def rd(directory, filename):
    with open(os.path.join(directory, filename), "rb") as f:
        return bytearray(f.read())


def ins_final(buf, final_indices):
    """Inserts 0x0D at `final_indices` (positions in the resulting buffer)."""
    out = bytearray(buf)
    for p in sorted(final_indices):
        out.insert(p, 0x0D)
    return out


def checksum16(data):
    return sum(data) & 0xFFFF


def solve_coupled_segs(re, ro, anchors):
    """
    anchors: list of (raw_e_idx, raw_o_idx, final_idx) strictly increasing,
             starting after (0,0,0) and ending before (len(re), len(ro), 32768).
    """
    pts = [(0, 0, 0)] + list(anchors) + [(len(re), len(ro), 32768)]
    oe_parts, oo_parts = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for idx in range(len(pts) - 1):
            re0, ro0, f0 = pts[idx]
            re1, ro1, f1 = pts[idx + 1]
            e_seg = re[re0:re1]
            o_seg = ro[ro0:ro1]
            M = f1 - f0
            ke = M - len(e_seg)
            ko = M - len(o_seg)
            assert ke >= 0 and ko >= 0, (
                f"Negative insert count in segment {idx}: "
                f"({re0}..{re1} -> len={len(e_seg)}, target={M}, ke={ke}), "
                f"({ro0}..{ro1} -> len={len(o_seg)}, target={M}, ko={ko})"
            )
            if ke == 0 and ko == 0:
                oe_parts.append(e_seg)
                oo_parts.append(o_seg)
                continue
            pe = os.path.join(tmp, f"e_{idx}.bin")
            po = os.path.join(tmp, f"o_{idx}.bin")
            ope = os.path.join(tmp, f"oe_{idx}.bin")
            opo = os.path.join(tmp, f"oo_{idx}.bin")
            with open(pe, "wb") as f: f.write(e_seg)
            with open(po, "wb") as f: f.write(o_seg)
            subprocess.run(
                [SOLVER_BIN, "coupled", pe, po, str(ke), str(ko), ope, opo],
                check=True,
            )
            with open(ope, "rb") as f: oe_parts.append(f.read())
            with open(opo, "rb") as f: oo_parts.append(f.read())
    return bytearray(b"".join(oe_parts)), bytearray(b"".join(oo_parts))


def solve_single_segs(raw, anchors, forbid_ranges=()):
    """Single-channel exact-count Viterbi between `(raw_idx, final_idx)` anchors."""
    forbid = bytearray(32768)
    for lo, hi in forbid_ranges:
        for i in range(lo, hi):
            forbid[i] = 1
    pts = [(0, 0)] + list(anchors) + [(len(raw), 32768)]
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for idx in range(len(pts) - 1):
            r0, f0 = pts[idx]
            r1, f1 = pts[idx + 1]
            seg = raw[r0:r1]
            M = f1 - f0
            k = M - len(seg)
            assert k >= 0, f"Single seg {idx} negative k={k}"
            if k == 0:
                parts.append(seg)
                continue
            pr = os.path.join(tmp, f"r_{idx}.bin")
            pf = os.path.join(tmp, f"fb_{idx}.bin")
            po = os.path.join(tmp, f"o_{idx}.bin")
            with open(pr, "wb") as f: f.write(seg)
            with open(pf, "wb") as f: f.write(forbid[f0:f1])
            subprocess.run([SOLVER_BIN, "single", pr, str(k), po, pf], check=True)
            with open(po, "rb") as f: parts.append(f.read())
    return bytearray(b"".join(parts))


def solve_lsb_chip(raw, e0, o0, e1, o1, anchors, forbid_ranges, has_q0=1, has_q1=1):
    forbid = bytearray(32768)
    for lo, hi in forbid_ranges:
        for i in range(lo, hi):
            forbid[i] = 1
    pts = [(0, 0)] + list(anchors) + [(len(raw), 32768)]
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for idx in range(len(pts) - 1):
            r0, f0 = pts[idx]
            r1, f1 = pts[idx + 1]
            seg = raw[r0:r1]
            M = f1 - f0
            k = M - len(seg)
            assert k >= 0, f"LSB seg {idx} negative k={k}"
            if k == 0:
                parts.append(seg)
                continue
            pr = os.path.join(tmp, "r.bin")
            pe0 = os.path.join(tmp, "e0.bin")
            po0 = os.path.join(tmp, "o0.bin")
            pe1 = os.path.join(tmp, "e1.bin")
            po1 = os.path.join(tmp, "o1.bin")
            pfb = os.path.join(tmp, "fb.bin")
            pout = os.path.join(tmp, "out.bin")
            with open(pr, "wb") as f: f.write(seg)
            with open(pe0, "wb") as f: f.write(e0[f0:f1])
            with open(po0, "wb") as f: f.write(o0[f0:f1])
            with open(pe1, "wb") as f: f.write(e1[f0:f1])
            with open(po1, "wb") as f: f.write(o1[f0:f1])
            with open(pfb, "wb") as f: f.write(forbid[f0:f1])
            subprocess.run(
                [
                    SOLVER_BIN, "lsb", pr, str(k),
                    pe0, po0, pe1, po1, pfb,
                    str(has_q0), str(has_q1), pout,
                ],
                check=True,
            )
            with open(pout, "rb") as f: parts.append(f.read())
    return bytearray(b"".join(parts))


def verify_metadata_d(l0, r0):
    full = bytearray(len(l0) * 2)
    full[0::2] = l0
    full[1::2] = r0
    assert full[8:16] == b"hobchim1"
    for i in range(33):
        off = 0x00B6 + i * 32
        num = struct.unpack(">H", full[off:off+2])[0]
        assert num == 60 + i, f"D Inst #{60+i} got {num}"
    for i in range(25):
        off = 0x0E20 + i * 32
        num, hi, lo = struct.unpack(">HHH", full[off:off+6])
        assert num == 59 + i, f"D KBD #{59+i} got {num}"
        kptr = (hi << 16) | lo
        assert full[kptr] == 0x14, f"D KBD #{num} @{kptr:04X} != 0x14"
    # Verify Wave Dir @0x3A06
    assert full[0x3A06:0x3A08] == b"\x10\x0C"
    for i in range(12):
        off = 0x3A08 + i * 32
        num, hi, lo = struct.unpack(">HHH", full[off:off+6])
        assert num == 650 + i
    assert full[0x3B88:0x3B8C] == b"\x04\x00\x09\x01"
    assert l0[0x7CD9:0x7CDE] == b"\x04\x08\xFE\x00\xF9"
    assert l0[0x7CF6:0x7CFB] == b"\x04\x08\xFE\x00\xFA"
    assert r0[0x7CD9:0x7CDD] == b"\x00\x00\x4A\x00"
    print("[OK] Block D Q0 metadata + Q0 Wave Descriptors (#650, #652, #653) 100% aligned")


def verify_metadata_c(l0, r0):
    full = bytearray(len(l0) * 2)
    full[0::2] = l0
    full[1::2] = r0
    assert full[8:16] == b"RKBLKB1B"
    assert full[0x001A:0x0024] == b"\x00\x0D\x01\x2C\x01\x58\x00\x00\x00\x50"
    assert full[0x0028:0x0032] == b"\x00\x0E\x01\x2C\x01\x43\x00\x00\x14\x52"
    assert full[0x0036:0x0040] == b"\x00\x10\x03\x94\x03\x9B\x00\x00\x60\x00"
    assert full[0x0050:0x0052] == b"\x0D\x30"
    for i in range(48):
        off = 0x0052 + i * 32
        num, hi, lo = struct.unpack(">HHH", full[off:off+6])
        assert num == 300 + i, f"C Inst #{300+i} got {num}"
        iptr = (hi << 16) | lo
        if iptr != 0:
            assert full[iptr] == 0x03 and full[iptr+3] in (0xF0, 0xF5), (
                f"C Inst #{num} @{iptr:04X}: {full[iptr:iptr+4].hex()}"
            )
    assert full[0x0F36:0x0F38] == b"\x4B\x0D"
    assert full[0x1450:0x1456] == b"\x00\x01\x0E\x28\x01\x2C"
    for i in range(24):
        off = 0x1454 + i * 32
        num, hi, lo = struct.unpack(">HHH", full[off:off+6])
        assert num == 300 + i, f"C KBD #{300+i} got {num}"
        kptr = (hi << 16) | lo
        if kptr != 0:
            assert full[kptr] == 0x14, f"C KBD #{num} @{kptr:04X} != 0x14 (got {full[kptr]:02X})"
            p0 = struct.unpack(">I", full[kptr+0x26:kptr+0x2A])[0]
            assert kptr < p0 < 0x6000, f"C KBD #{num} @{kptr:04X} invalid p0=0x{p0:08X}"
    assert full[0x6000:0x6002] == b"\x10\x08"
    for i in range(8):
        off = 0x6002 + i * 32
        num = struct.unpack(">H", full[off:off+2])[0]
        assert num == 0x0394 + i
    assert full[0x6102:0x6106] == b"\x04\xF1\x0D\x01"
    assert l0[0x4834:0x4837] == b"\x04\x0D\x01"
    assert l0[0x5867:0x586B] == b"\x04\x0D\x01\x00"
    assert l0[0x58AA:0x58AE] == b"\x04\x0D\x01\x00"
    assert l0[0x58C7:0x58CB] == b"\x04\x0D\x01\x00"
    assert l0[0x6258:0x625C] == b"\x04\x09\xFF\x00"
    assert l0[0x7FC1:0x7FC5] == b"\x04\x09\xFF\x00"
    assert l0[0x7FDE:0x7FE2] == b"\x04\x08\xFF\x00"
    print("[OK] Block C Q0 metadata (header, Inst #300..#347, KBD #300..#323, Wave #0394..#039B) 100% aligned")


def reconstruct_block_d():
    print("\n=== Reconstructing Sound Block D (hobchim1) ===")
    de_raw = rd(DIR_D_IN, "106K250")
    do_raw = rd(DIR_D_IN, "101K250")

    l0_m = ins_final(de_raw, [0x5A, 0x5D, 0x6D, 0x7D, 0x8D, 0xD48])
    r0_m = ins_final(do_raw, [0x14])
    assert l0_m[0x1DC4:0x1DC6] == b"\x04\x09"

    # Compute exact raw offsets after metadata + #650 envelope table (at final 0x1E01 = byte 0x003C02):
    # Wave #652 @ final 0x7CD9 (`04 08 fe 00 f9`):
    # In de6 (`+6`), #652 starts at 0x7BF7 -> raw de_raw index = 0x7BF7 - 6 = 0x7BF1!
    # Wave #653 ends at final 0x7D13 (`+58` bytes: `0x7CD9 + 58 = 0x7D13`), in de6 at 0x7C31 -> raw de_raw = 0x7C2B!
    # In do1 (`+1`), #652 (`00 00 4A 00 DC...`) starts at 0x7BD8 (`7BD8..7C12` = 58B)!
    assert l0_m[0x7BF7:0x7BFC] == b"\x04\x08\xFE\x00\xF9"
    assert r0_m[0x7BD8:0x7BDC] == b"\x00\x00\x4A\x00"
    # Now in l0_m/r0_m coordinates:
    # 0x1E01 -> (0x1E01, 0x1E01, 0x1E01)  [past metadata + #650 descriptor + envelope table]
    # 0x7CD9 -> l0_m[0x7BF7], r0_m[0x7BD8] (#652 start)
    # 0x7D13 -> l0_m[0x7C31], r0_m[0x7C12] (#653 end, audio resumes!)
    l0, r0 = solve_coupled_segs(
        l0_m, r0_m,
        [(0x1E01, 0x1E01, 0x1E01), (0x7BF7, 0x7BD8, 0x7CD9), (0x7C31, 0x7C12, 0x7D13)],
    )
    verify_metadata_d(l0, r0)

    # Q1: 105K250 (L1) & 100K250 (R1) with exact structural wave-boundary anchors
    l1_raw = rd(DIR_D_IN, "105K250")
    r1_raw = rd(DIR_D_IN, "100K250")
    assert l1_raw[0x29B7:0x29BC] == b"\x04\x08\xFF\x00\x54"
    assert r1_raw[0x29C2:0x29C6] == b"\x00\x01\xFA\x01"
    assert l1_raw[0x7286:0x728B] == b"\x04\x08\xFE\x00\xE7"
    assert r1_raw[0x7281:0x7285] == b"\x00\x00\x40\x01"
    assert l1_raw[0x7E59:0x7E5E] == b"\x04\x09\xFF\x00\xFF"
    assert r1_raw[0x7E4D:0x7E52] == b"\x00\x00\x3F\x01\x14"
    l1, r1 = solve_coupled_segs(
        l1_raw, r1_raw,
        [
            (0x29B7, 0x29C2, 0x2A12),  # #651 desc start
            (0x29DC, 0x29E7, 0x2A37),  # #651 audio start
            (0x63E2, 0x63D6, 0x64CF),  # #652 LOBDF4 start (0x01C99E)
            (0x6B34, 0x6B30, 0x6C33),  # #653 LOBG4 start (0x01D866)
            (0x7286, 0x7281, 0x7391),  # #654 desc start
            (0x72A3, 0x729E, 0x73AE),  # #654 OBB4 audio start
            (0x7709, 0x76FE, 0x7818),  # #654 OBB4 seg0->seg1 (0x01F030)
            (0x79F9, 0x79EF, 0x7B0B),  # #655 OBEF5 start (0x01F616)
            (0x7E59, 0x7E4D, 0x7F75),  # #658..#661 desc start
        ],
    )

    # Q2: 104K250 (L2) & 099K250 (R2) with exact structural wave-boundary anchors
    l2_raw = rd(DIR_D_IN, "104K250")
    r2_raw = rd(DIR_D_IN, "099K250")
    assert l2_raw[0x4CC2:0x4CC7] == b"\x04\x08\xFE\x00\x9B"
    assert r2_raw[0x4CDE:0x4CE2] == b"\x00\x00\x39\x02"
    l2, r2 = solve_coupled_segs(
        l2_raw, r2_raw,
        [
            (0x4CC2, 0x4CDE, 0x4DD2),                  # #655..#657 desc start
            (0x4D19, 0x4D35, 0x4E29),                  # #657 OBDF6 seg1 start
            (0x5007, 0x5025, 0x511A),                  # #658 CHA3 seg1 start (0x02A234)
            (0x5B9F, 0x5BC4, 0x5CD6),                  # #661 CHDS5 seg0 start (0x02B9AC)
            (len(l2_raw) - 2, len(r2_raw) - 2, 32766),  # trailing 00 00
        ],
    )

    # Q3: 103K250 (L3) & 098K250 (R3) with exact structural wave-boundary anchors
    l3_raw = rd(DIR_D_IN, "103K250")
    r3_raw = rd(DIR_D_IN, "098K250")
    l3, r3 = solve_coupled_segs(
        l3_raw, r3_raw,
        [
            (0x2A, 0x2A, 0x2A),                        # 42 zeros at start of #656 OBG5
            (0x073D, 0x0740, 0x0758),                  # #657 OBDF6 seg0 start (0x030EB0)
            (0x0BA1, 0x0BA2, 0x0BC2),                  # #658 CHA3 seg0 start (0x031784)
            (0x28C2, 0x28B0, 0x290E),                  # #659 CHDS4 seg0 start (0x03521C)
            (len(l3_raw) - 4, len(r3_raw) - 4, 32764),  # trailing 00 00 00 00
        ],
    )

    # LSB0 (107K250, miss 102) & LSB1 (102K250, miss 127)
    lsb0_raw = rd(DIR_D_IN, "107K250")
    lsb1_raw = rd(DIR_D_IN, "102K250")
    lsb0 = solve_lsb_chip(
        lsb0_raw, l0, r0, l1, r1,
        anchors=[(0x7F75 - 102, 0x7F75)] if (len(lsb0_raw) >= 0x7F75 - 102 and all((b & 0xF0) == 0 for b in lsb0_raw[0x7F75 - 102:])) else [],
        forbid_ranges=[(0x0000, 0x1E01), (0x7CD9, 0x7D13), (0x7F75, 0x8000)],
        has_q0=1, has_q1=1,
    )
    lsb1 = solve_lsb_chip(
        lsb1_raw, l2, r2, l3, r3,
        anchors=[],
        forbid_ranges=[(0x4DD2, 0x4E29), (0x7FFC, 0x8000)],
        has_q0=1, has_q1=1,
    )

    os.makedirs(DIR_D_OUT, exist_ok=True)
    chips_d = {
        "107K250": lsb0, "106K250": l0, "105K250": l1, "104K250": l2, "103K250": l3,
        "102K250": lsb1, "101K250": r0, "100K250": r1, "099K250": r2, "098K250": r3,
    }
    ok_d = 0
    for name in sorted(chips_d):
        data = bytes(chips_d[name])
        assert len(data) == 32768, f"{name} len {len(data)}"
        cs = checksum16(data)
        exp = EXPECTED_D[name]
        match = (cs == exp)
        if match: ok_d += 1
        with open(os.path.join(DIR_D_OUT, name), "wb") as f:
            f.write(data)
        print(f"  {name}: len={len(data)} csum=0x{cs:04X} expected=0x{exp:04X} {'MATCH' if match else 'MISMATCH!'}")
    assert ok_d == 10, f"Block D matches {ok_d}/10"


def reconstruct_block_c():
    print("\n=== Reconstructing Sound Block C (RKBLKB1B) ===")
    ce_raw = rd(DIR_C_IN, "085K250")
    co_raw = rd(DIR_C_IN, "080K250")

    # L0 (085K250) metadata [0..0x3000] = 12 inserts total:
    # - 4 in Inst Dir (final ce indices: [0x1CA, 0x1DA, 0x1EA, 0x20A])
    # - 4 at end of Inst #300 (`ce_final` indices `[0xA21..0xA24]`, right before `00 01` at `0xA28` and `0E 28` at `0xA29`)
    #   Wait: let's check exact final positions!
    #   Before `#300`, `ce4` had 4 inserts (`[0x1CA..0x20A]`). After `#300` ends at `ce4[0x0A24]`, we insert 4 `0x0D`s
    #   at `ce4[0x0A25]` -> final indices `[0xA25, 0xA26, 0xA27, 0xA28]` ( wait: let's check what moves to `0xA29` = `0E`!).
    #   In `ce4`, `0E` is at `0xA25`, and we need `0E` at `0xA29` -> so `[0xA25, 0xA26, 0xA27, 0xA28]` moves `ce4[0xA25]` to `0xA29`!
    # - 4 in KBD #308 (`ce4` between `0x2226` (`+0x237`) and `0x2277`): inserting at final `[0x2249..0x224C]`?
    #   Let's check KBD #308 keys around `0x2236..0x227A`: remember `08 00` is at `ce8[0x2225]`, `+253` (`02 03 02 01...`) is at `+0x253` in unaligned `k308`!
    #   Wait: `0x210F + (0x238//2) = 0x222B`! Look at `k308` at `+238`: `10 zeros` before `15`! In `#309` (`+237..+249`), `1F` occupied `+237..+242`, `00` occupied `+243..+248` (`6` zeros), then `18`.
    #   In `#308`, `15` (`21`) occupied `+241..+248`, then zeros, then `+253`: `02 03 02 01...`, then `+27B`: `d9 d9 d9 d9` (`8` bytes early vs `#307`'s `+283`)!
    #   So inserting 4 in `ce` and 4 in `co` at final chip indices `[0x2241, 0x2242, 0x2243, 0x2244]` (`0x210F + 0x132..0x135` -> between `+25F` and `+27B`, where `#308` velocity/keymap has a 4-word `0D 0D` block!) restores `#307` (`0x2299`), `#306`..`#301`, AND puts `Wave Dir` at `0x3000` (`0x6000`)!
    # Verify R0 (080K250) metadata [0..0x3000] = 7 inserts total:
    # - 1 in Inst #319 (`00 0D` at `co` final index `0x079A`, right after `00 4E` at `0x0799`!)
    # - 2 in Inst #300 (right before `01` at `co0[0x0A25]` -> moves `co0[0x0A25]` to `0x0A28` (`1` + `2` = +3 -> `0x0A28`!), wait: so final indices `[0xA26, 0xA27]`!)
    # - 4 in KBD #308 (`[0x2241, 0x2242, 0x2243, 0x2244]`)!
    l0_meta_pos = [0x0028, 0x01CB, 0x01DB, 0x01EB, 0x020B, 0x0699, 0x06C1, 0x0739, 0x2241, 0x2242, 0x2243, 0x2244]
    r0_meta_pos = [0x000D, 0x0343, 0x079B, 0x0BA9, 0x2241, 0x2242, 0x2243]
    l0_m = ins_final(ce_raw, l0_meta_pos)
    r0_m = ins_final(co_raw, r0_meta_pos)

    # Insert the 5 deterministic missing 0x0D bytes at offset +1 (after 0x04) of drum descriptors #916..#920
    # Coordinates in l0_m (each prior insert shifts subsequent indices by +1):
    #   #916 (0x3081): insert at 0x3082
    #   #917 (0x4801 -> 0x4802): insert at 0x4803
    #   #918 (0x5802 -> 0x5804): insert at 0x5805
    #   #919 (0x5844 -> 0x5847): insert at 0x5848
    #   #920 (0x5860 -> 0x5864): insert at 0x5865
    l0_m = ins_final(l0_m, [0x3082, 0x4803, 0x5805, 0x5848, 0x5865])

    assert l0_m[0x3000] == 0x10 and r0_m[0x3000] == 0x08
    assert l0_m[0x3081:0x3085] == b"\x04\x0D\x01\x00"   # #916 SNARE2
    assert r0_m[0x3081:0x3085] == b"\xF1\x01\x3C\x00"
    assert l0_m[0x4802:0x4806] == b"\x04\x0D\x01\x00"   # #917 RIDERIM2
    assert r0_m[0x4803:0x4807] == b"\xFA\x01\x23\x00"
    assert l0_m[0x5804:0x5808] == b"\x04\x0D\x01\x00"   # #918 CRASH2
    assert r0_m[0x57FB:0x57FF] == b"\x00\x01\x23\x00"
    assert l0_m[0x5847:0x584B] == b"\x04\x0D\x01\x00"   # #919 RIMSHOT
    assert r0_m[0x583E:0x5842] == b"\xF1\x01\x97\x00"
    assert l0_m[0x5864:0x5868] == b"\x04\x0D\x01\x00"   # #920 CLAP
    assert r0_m[0x585B:0x585F] == b"\xF4\x01\x97\x00"
    assert l0_m[0x61CF:0x61D3] == b"\x04\x09\xFF\x00"   # #921 MMC2
    assert r0_m[0x61C2:0x61C6] == b"\x00\x00\xFA\x00"
    assert l0_m[-63:-59] == b"\x04\x09\xFF\x00"         # #922/#923 MMC3/MMC4
    assert r0_m[-63:-59] == b"\x00\x00\xF9\x00"

    l0, r0 = solve_coupled_segs(
        l0_m, r0_m,
        [
            (0x309E, 0x309E, 0x309E),  # end of #916 SNARE2 desc (0x613C)
            (0x4802, 0x4803, 0x4834),  # start of #917 RIDERIM2 desc (0x9068)
            (0x4835, 0x4836, 0x4867),  # end of #917 RIDERIM2 desc (0x90CE)
            (0x5804, 0x57FB, 0x5867),  # start of #918 CRASH2 desc (0xB0CE)
            (0x5847, 0x583E, 0x58AA),  # start of #919 RIMSHOT desc (0xB154)
            (0x5864, 0x585B, 0x58C7),  # start of #920 CLAP desc (0xB18E)
            (0x5881, 0x5878, 0x58E4),  # end of #920 CLAP desc (0xB1C8)
            (0x61CF, 0x61C2, 0x6258),  # start of #921 MMC2 desc (0xC4B0)
            (0x61EC, 0x61DF, 0x6275),  # end of #921 MMC2 desc (0xC4EA)
            (len(l0_m) - 63, len(r0_m) - 63, 0x7FC1),  # #922/#923 desc start
        ],
    )
    verify_metadata_c(l0, r0)

    # Q1: 084K250 (L1, miss 516) & 079K250 (R1, miss 526)
    l1_raw = rd(DIR_C_IN, "084K250")
    r1_raw = rd(DIR_C_IN, "079K250")
    l1, r1 = solve_coupled_segs(l1_raw, r1_raw, [])

    # Q2: 083K250 (L2 = source blank 0xFB all 32768B, preserved untouched!), 078K250 (R2, miss 208)
    l2 = rd(DIR_C_IN, "083K250")
    r2_raw = rd(DIR_C_IN, "078K250")
    # R2 ends with 36 zeros `0x7F0C..0x7F30` -> final `0x7FDC..0x8000` (all 208 inserts before 0x7F0C!)
    assert all(b == 0 for b in r2_raw[0x7F0C:])
    r2 = solve_single_segs(r2_raw, anchors=[(0x7F0C, 0x7FDC)], forbid_ranges=[(0x7FDC, 0x8000)])

    # Q3: 082K250 (L3, miss 156) & 077K250 (R3, miss 132)
    l3_raw = rd(DIR_C_IN, "082K250")
    r3_raw = rd(DIR_C_IN, "077K250")
    # Both share exact 5058-byte zero silence run at tail (`0x6BA2..0x7F64` in L3, `0x6BBA..0x7F7C` in R3 -> final `0x6C3E`)
    # plus matched attack after zero silence at `0x233A` (`082`) / `0x2338` (`077`) (`e3 e2 e2 e1...`)!
    assert all(b == 0 for b in l3_raw[0x6BA2:])
    assert all(b == 0 for b in r3_raw[0x6BBA:])
    # Note: 0x233A in 082 (~22 misses before 0x233A) and 0x2338 in 077 (~24 misses) - let's let DP find exact offset before 0x6C3E!
    l3, r3 = solve_coupled_segs(l3_raw, r3_raw, [(0x6BA2, 0x6BBA, 0x6C3E)])

    # LSB0 (086K250, miss 93) & LSB1 (081K250, miss 402)
    lsb0_raw = rd(DIR_C_IN, "086K250")
    lsb1_raw = rd(DIR_C_IN, "081K250")
    lsb0 = solve_lsb_chip(
        lsb0_raw, l0, r0, l1, r1,
        anchors=[],
        forbid_ranges=[(0x0000, 0x309E), (0x4834, 0x4867), (0x5867, 0x58E4), (0x6258, 0x6275), (0x7FC1, 0x8000)],
        has_q0=1, has_q1=1,
    )
    # Note: In Q2/Q3 LSB1 (081K250), trailing zeros start at 0x7E4A (len 32366 -> EOF has 36 zeros) -> anchor at 0x7E4A -> 0x7FDC!
    assert all(b == 0 for b in lsb1_raw[0x7E4A:])
    lsb1 = solve_lsb_chip(
        lsb1_raw, l2, r2, l3, r3,
        anchors=[(0x7E4A, 0x7FDC)],
        forbid_ranges=[(0x7FDC, 0x8000)],
        has_q0=0, has_q1=1,
    )

    os.makedirs(DIR_C_OUT, exist_ok=True)
    chips_c = {
        "086K250": lsb0, "085K250": l0, "084K250": l1, "083K250": l2, "082K250": l3,
        "081K250": lsb1, "080K250": r0, "079K250": r1, "078K250": r2, "077K250": r3,
    }
    ok_c = 0
    for name in sorted(chips_c):
        data = bytes(chips_c[name])
        assert len(data) == 32768, f"{name} len {len(data)}"
        cs = checksum16(data)
        exp = EXPECTED_C[name]
        match = (cs == exp)
        if match: ok_c += 1
        with open(os.path.join(DIR_C_OUT, name), "wb") as f:
            f.write(data)
        note = " (BLANK 0xFB SOURCE PRESERVED)" if name == "083K250" else ("MATCH" if match else "MISMATCH!")
        print(f"  {name}: len={len(data)} csum=0x{cs:04X} expected=0x{exp:04X} {note}")
    assert ok_c == 9, f"Block C matches {ok_c}/9 (excluding 083K250)"


def main() -> int:
    global ROMS_BASE, DIR_D_IN, DIR_C_IN, DIR_D_OUT, DIR_C_OUT
    ap = argparse.ArgumentParser(
        description="Reconstruct CR-stripped (0x0D) K250 Sound Block D/C EPROM dumps via coupled Viterbi solver."
    )
    ap.add_argument(
        "--roms-dir",
        default=os.environ.get("K250_SOUNDS_BINARIES_DIR", "."),
        help="Base directory containing \"K250 Sound Block D\" and \"K250 Sound Block C\" (or env K250_SOUNDS_BINARIES_DIR)",
    )
    ap.add_argument("--block-d-in", help="Override input directory for Sound Block D")
    ap.add_argument("--block-c-in", help="Override input directory for Sound Block C")
    ap.add_argument("--block-d-out", help="Override output directory for Sound Block D (Reconstructed)")
    ap.add_argument("--block-c-out", help="Override output directory for Sound Block C (Reconstructed)")
    ap.add_argument("--block", choices=("all", "d", "c"), default="all", help="Which block(s) to reconstruct")
    ap.add_argument("--cc", default=os.environ.get("CC", "cc"), help="C compiler binary (default: cc)")
    args = ap.parse_args()

    ROMS_BASE = args.roms_dir
    DIR_D_IN = args.block_d_in or os.path.join(ROMS_BASE, "K250 Sound Block D")
    DIR_C_IN = args.block_c_in or os.path.join(ROMS_BASE, "K250 Sound Block C")
    DIR_D_OUT = args.block_d_out or os.path.join(ROMS_BASE, "K250 Sound Block D (Reconstructed)")
    DIR_C_OUT = args.block_c_out or os.path.join(ROMS_BASE, "K250 Sound Block C (Reconstructed)")

    compile_solver(cc=args.cc)
    if args.block in ("all", "d"):
        reconstruct_block_d()
    if args.block in ("all", "c"):
        reconstruct_block_c()
    print("\nAll reconstructed Sound Block images written and verified!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
