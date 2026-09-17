"""연구 스크립트 panel_analyze 단위 테스트

절대 규칙: 장중 전체 pytest 금지. python3 -m unittest tests/research/test_panel_analyze.py 로 실행.
"""
from __future__ import annotations

import sys
import os
import unittest

# scripts/research 경로를 sys.path 에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../scripts/research")))

from panel_analyze import asof, episodes, quantile_match_thresholds


class TestPanelAnalyze(unittest.TestCase):

    def test_episodes_merge(self):
        """episodes() 병합: 연속/비연속/방향 바뀜 검증."""
        # 1. 연속 5분봉 동일 방향 -> 1건으로 병합
        panel_consec = [
            {"sym": "005930", "ts": "2026-09-16T09:00:00"},
            {"sym": "005930", "ts": "2026-09-16T09:05:00"},
            {"sym": "005930", "ts": "2026-09-16T09:10:00"},
        ]
        eps1 = episodes(panel_consec, lambda r: 1)
        self.assertEqual(len(eps1), 1)
        self.assertEqual(eps1[0]["ts"], "2026-09-16T09:00:00")
        self.assertEqual(eps1[0]["_dir"], 1)

        # 2. 비연속 (5분 초과 간격) -> 분리
        panel_gap = [
            {"sym": "005930", "ts": "2026-09-16T09:00:00"},
            {"sym": "005930", "ts": "2026-09-16T09:15:00"},
        ]
        eps2 = episodes(panel_gap, lambda r: 1)
        self.assertEqual(len(eps2), 2)
        self.assertEqual([e["ts"] for e in eps2], ["2026-09-16T09:00:00", "2026-09-16T09:15:00"])

        # 3. 방향 바뀜 (+1 -> -1) -> 분리
        panel_flip = [
            {"sym": "005930", "ts": "2026-09-16T09:00:00", "dir": 1},
            {"sym": "005930", "ts": "2026-09-16T09:05:00", "dir": -1},
        ]
        eps3 = episodes(panel_flip, lambda r: r["dir"])
        self.assertEqual(len(eps3), 2)
        self.assertEqual(eps3[0]["_dir"], 1)
        self.assertEqual(eps3[1]["_dir"], -1)

    def test_asof_boundary(self):
        """asof() 경계 검증: 시각 <= t 인 마지막 값 (같은 시각 포함)."""
        series = [
            ("2026-09-16T11:00:00", {"val": 100}),
            ("2026-09-16T11:05:00", {"val": 200}),
            ("2026-09-16T11:10:00", {"val": 300}),
        ]
        # 같은 시각 포함
        self.assertEqual(asof(series, "2026-09-16T11:05:00"), {"val": 200})
        # 시각 직전
        self.assertEqual(asof(series, "2026-09-16T11:04:59"), {"val": 100})
        # 시각 직후
        self.assertEqual(asof(series, "2026-09-16T11:05:01"), {"val": 200})
        # 첫 시각 이전 -> None
        self.assertIsNone(asof(series, "2026-09-16T10:59:59"))
        # 마지막 시각 이후 -> 마지막 값
        self.assertEqual(asof(series, "2026-09-16T11:15:00"), {"val": 300})

    def test_quantile_match_thresholds(self):
        """T1 분위수 임계 계산 검증."""
        # N=10, whale_ratio 가 0.4 이상인 행이 2개 (상위 20%, q=0.8)
        panel = [
            {"whale_ratio": 0.50, "mid_ratio": 0.35, "ant_ratio": 0.25},  # top 1
            {"whale_ratio": 0.42, "mid_ratio": 0.30, "ant_ratio": 0.20},  # top 2
            {"whale_ratio": 0.30, "mid_ratio": 0.25, "ant_ratio": 0.15},
            {"whale_ratio": 0.20, "mid_ratio": 0.20, "ant_ratio": 0.12},
            {"whale_ratio": 0.15, "mid_ratio": 0.15, "ant_ratio": 0.10},
            {"whale_ratio": 0.10, "mid_ratio": 0.12, "ant_ratio": 0.08},
            {"whale_ratio": 0.05, "mid_ratio": 0.10, "ant_ratio": 0.05},
            {"whale_ratio": 0.02, "mid_ratio": 0.05, "ant_ratio": 0.03},
            {"whale_ratio": 0.01, "mid_ratio": 0.02, "ant_ratio": 0.02},
            {"whale_ratio": 0.00, "mid_ratio": 0.01, "ant_ratio": 0.01},
        ]
        q, th_w, th_m, th_a, w_cnt, N = quantile_match_thresholds(panel, 0.4)
        self.assertEqual(N, 10)
        self.assertEqual(w_cnt, 2)
        self.assertAlmostEqual(q, 0.8)
        self.assertEqual(th_w, 0.4)
        # rank 10 - 2 = 8번째 index (0-based) -> sorted[8] = 0.30
        self.assertAlmostEqual(th_m, 0.30)
        self.assertAlmostEqual(th_a, 0.20)

    def test_date_boundary_not_merged(self):
        """T3 날짜 경계 비병합 검증: 날짜가 다르면 연속 시각이라도 병합하지 않는다."""
        # 같은 종목, 09-16 마지막 봉과 09-17 첫 봉
        panel = [
            {"sym": "005930", "ts": "2026-09-16T15:20:00"},
            {"sym": "005930", "ts": "2026-09-17T09:00:00"},
        ]
        eps = episodes(panel, lambda r: 1)
        self.assertEqual(len(eps), 2)
        self.assertEqual(eps[0]["ts"], "2026-09-16T15:20:00")
        self.assertEqual(eps[1]["ts"], "2026-09-17T09:00:00")

        # 만약 시각적으로 바로 다음 분봉 형태(예: 자정 경계)라 하더라도 날짜가 다르면 분리
        panel_midnight = [
            {"sym": "005930", "ts": "2026-09-16T23:55:00"},
            {"sym": "005930", "ts": "2026-09-17T00:00:00"},
        ]
        eps_mid = episodes(panel_midnight, lambda r: 1)
        self.assertEqual(len(eps_mid), 2)


if __name__ == "__main__":
    unittest.main()
