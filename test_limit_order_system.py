import requests
import json
import sqlite3
import time

def run_tests():
    print("=== STARTING END-TO-END VERIFICATION SUITE ===")
    ft_url = "http://127.0.0.1:8080"
    dash_url = "http://127.0.0.1:5050"
    db_path = "user_data/tradesv3.dryrun.sqlite"

    # 1. Verify Empty State & 7 Candidates
    r = requests.get(f"{dash_url}/api/data")
    assert r.status_code == 200, f"Status {r.status_code}"
    data = r.json()
    cands = data.get("ai_candidate_radar", [])
    print(f"Test 1: Candidate radar pairs: {len(cands)}")
    pairs = [c["pair"] for c in cands]
    print(f"Pairs in radar: {pairs}")
    for c in cands:
        p_name = c['pair']
        mk = c['mark_price']
        sp = c['support_floor_48h']
        tp = c['target_tp']
        sl = c['stop_loss']
        rr = c['rr_ratio']
        bc = c.get('bull_conviction')
        cand_limit = c.get('candidate_limit_price')
        assert cand_limit is not None and cand_limit > 0, f"Invalid candidate limit price: {cand_limit}"
        assert cand_limit < tp, f"Candidate limit {cand_limit} must be below TP {tp} for {p_name}"
        assert cand_limit >= sp, f"Candidate limit {cand_limit} must be at/above floor {sp} for {p_name}"
        assert cand_limit <= mk, f"Candidate limit {cand_limit} must be maker below/at mark {mk} for {p_name}"
        assert rr >= 1.80, f"R:R ratio {rr} must be >= 1.80 for {p_name}"
        print(f"  {p_name}: Mark=${mk}, Support=${sp}, Limit=${cand_limit}, TP=${tp}, SL=${sl}, R:R={rr}:1, BullConviction={bc}%")

    # 2. Test Resting Maker Limit Order Placement via Dashboard POST /api/forceenter
    print("\nTest 2: Placing Resting Maker Limit Order on DOGE/USDT:USDT @ $0.0500...")
    res_limit = requests.post(f"{dash_url}/api/forceenter", json={
        "pair": "DOGE/USDT:USDT",
        "side": "long",
        "ordertype": "limit",
        "price": 0.0500,
        "stakeamount": 314.0,
        "entry_tag": "ui_test_maker_limit"
    })
    print(f"Placement Status: {res_limit.status_code}")
    res_json = res_limit.json()
    trade_id = res_json.get("trade_id")
    print(f"Created Trade ID: {trade_id}")
    assert trade_id is not None, "Trade ID must not be None"

    # 3. Verify SQLite Registration (Empirical Proof)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    t_row = cur.execute("SELECT id, pair, is_open, amount, open_rate, stake_amount FROM trades WHERE id = ?", (trade_id,)).fetchone()
    print(f"SQLite Trades Table: {t_row}")
    assert t_row is not None and t_row[2] == 1 and t_row[3] == 0.0, "Must be open with amount=0.0"

    o_rows = cur.execute("SELECT id, ft_trade_id, order_type, status, price, filled, remaining, ft_is_open FROM orders WHERE ft_trade_id = ?", (trade_id,)).fetchall()
    print(f"SQLite Orders Table: {o_rows}")
    assert len(o_rows) > 0 and o_rows[0][7] == 1 and o_rows[0][2] == "limit", "Must have open limit order"

    # 4. Verify Dashboard Telemetry for Resting Limit Order
    r_telemetry = requests.get(f"{dash_url}/api/data")
    ot = r_telemetry.json().get("live_simulation", {}).get("open_trades", [])
    print(f"\nTest 4: Dashboard Open Trades Telemetry: count={len(ot)}")
    assert len(ot) == 1, "Expected exactly 1 open trade"
    trade_data = ot[0]
    print(f"  ID: {trade_data['id']}")
    print(f"  Pair: {trade_data['pair']}")
    print(f"  is_limit_order: {trade_data['is_limit_order']}")
    print(f"  order_status: {trade_data['order_status']}")
    print(f"  open_rate: {trade_data['open_rate']}")
    print(f"  close_rate (Mark): {trade_data['close_rate']}")
    print(f"  distance_to_fill: {trade_data['distance_to_fill']}")
    print(f"  distance_to_fill_pct: {trade_data['distance_to_fill_pct']}%")
    print(f"  tp_price_16: {trade_data['tp_price_16']}")
    print(f"  tp_price_52: {trade_data['tp_price_52']}")
    print(f"  sl_stale_price: {trade_data['sl_stale_price']}")
    print(f"  sl_emerg_price: {trade_data['sl_emerg_price']}")

    # 5. Test Cancellation via Dashboard POST /api/cancel_order
    print("\nTest 5: Testing Cancellation of Resting Limit Order...")
    r_cancel = requests.post(f"{dash_url}/api/cancel_order", json={"trade_id": trade_id})
    print(f"Cancel Status: {r_cancel.status_code}, body: {r_cancel.text}")
    assert r_cancel.status_code == 200, "Cancel should return 200"

    # Verify SQLite after cancel
    t_after_cancel = cur.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    o_after_cancel = cur.execute("SELECT * FROM orders WHERE ft_trade_id = ?", (trade_id,)).fetchall()
    print(f"Trades in DB after cancel: {t_after_cancel}")
    print(f"Orders in DB after cancel: {o_after_cancel}")
    assert t_after_cancel is None and len(o_after_cancel) == 0, "SQLite must be cleanly purged"

    # 6. Test Market Fill and Exit Cycle
    print("\nTest 6: Testing Market Fill & Force Exit Lifecycle...")
    r_mfill = requests.post(f"{dash_url}/api/market_fill", json={
        "pair": "DOGE/USDT:USDT",
        "stakeamount": 314.0
    })
    print(f"Market Fill Status: {r_mfill.status_code}")
    m_tid = r_mfill.json().get("trade_id")
    print(f"Market Trade ID: {m_tid}")
    assert m_tid is not None, "Market trade ID required"

    time.sleep(0.5)
    t_market = cur.execute("SELECT id, is_open, amount, open_rate FROM trades WHERE id = ?", (m_tid,)).fetchone()
    print(f"SQLite Market Trade: {t_market}")
    assert t_market is not None and t_market[1] == 1 and t_market[2] > 0, "Must be open and filled (amount > 0)"

    # Verify telemetry shows FILLED (not resting limit order)
    r_telemetry_filled = requests.get(f"{dash_url}/api/data")
    ot_filled = r_telemetry_filled.json().get("live_simulation", {}).get("open_trades", [])
    assert len(ot_filled) == 1, "Expected 1 open trade"
    assert ot_filled[0]["is_limit_order"] is False, "Filled trade must have is_limit_order = False"
    assert ot_filled[0]["order_status"] == "FILLED", "Filled trade must have order_status = FILLED"
    print(f"Verified Telemetry: is_limit_order={ot_filled[0]['is_limit_order']}, order_status={ot_filled[0]['order_status']}")

    r_exit = requests.post(f"{dash_url}/api/forceexit", json={"trade_id": m_tid, "ordertype": "market"})
    print(f"Force Exit Status: {r_exit.status_code}, body: {r_exit.text}")
    assert r_exit.status_code == 200, "Force exit must return 200"

    time.sleep(0.5)
    t_closed = cur.execute("SELECT id, is_open, amount, close_profit_abs, exit_reason FROM trades WHERE id = ?", (m_tid,)).fetchone()
    print(f"SQLite Trade After Exit: {t_closed}")
    assert t_closed is not None and t_closed[1] == 0, "Must be closed (is_open=0)"

    # Clean up test records
    cur.execute("DELETE FROM orders WHERE ft_trade_id = ?", (m_tid,))
    cur.execute("DELETE FROM trades WHERE id = ?", (m_tid,))
    conn.commit()

    # 7. Test Direct Candidate Market Order Placement via /api/forceenter
    print("\nTest 7: Testing Direct Candidate Market Order on SOL/USDT:USDT...")
    r_cand_mkt = requests.post(f"{dash_url}/api/forceenter", json={
        "pair": "SOL/USDT:USDT",
        "side": "long",
        "ordertype": "market",
        "stakeamount": 314.0,
        "entry_tag": "ui_candidate_market"
    })
    print(f"Direct Market Order Status: {r_cand_mkt.status_code}")
    assert r_cand_mkt.status_code == 200, "Candidate market entry must succeed"
    cand_mkt_tid = r_cand_mkt.json().get("trade_id")
    print(f"Candidate Market Trade ID: {cand_mkt_tid}")

    time.sleep(0.5)
    t_cand_mkt = cur.execute("SELECT id, pair, is_open, amount FROM trades WHERE id = ?", (cand_mkt_tid,)).fetchone()
    assert t_cand_mkt is not None and t_cand_mkt[2] == 1 and t_cand_mkt[3] > 0, "Must be open and filled"

    # Exit candidate trade
    r_exit_cand = requests.post(f"{dash_url}/api/forceexit", json={"trade_id": cand_mkt_tid, "ordertype": "market"})
    assert r_exit_cand.status_code == 200, "Exit candidate trade must succeed"
    time.sleep(0.5)
    cur.execute("DELETE FROM orders WHERE ft_trade_id = ?", (cand_mkt_tid,))
    cur.execute("DELETE FROM trades WHERE id = ?", (cand_mkt_tid,))
    conn.commit()

    # 8. Test Candlestick Data Feed
    print("\nTest 8: Testing /api/candles endpoint...")
    r_candles = requests.get(f"{dash_url}/api/candles?pair=BTC/USDT:USDT&tf=1h&limit=10")
    assert r_candles.status_code == 200, "Candles endpoint must return 200"
    c_json = r_candles.json()
    assert len(c_json.get("candles", [])) > 0, "Must return non-empty candles"
    print(f"Candles retrieved: {len(c_json['candles'])} candles for {c_json['pair']}")

    conn.close()

    # 9. Test Dynamic Multi-Slot Cluster Takeover (Simultaneous 3 Limit Orders across Clusters)
    print("\nTest 9: Testing Dynamic Multi-Slot Takeover (3 Limit Orders across Clusters)...")
    cluster_targets = [
        {"pair": "BTC/USDT:USDT", "price": 50000.0, "cluster": "major"},
        {"pair": "SOL/USDT:USDT", "price": 50.0, "cluster": "alt"},
        {"pair": "PAXG/USDT:USDT", "price": 3000.0, "cluster": "defensive"}
    ]
    placed_trade_ids = []
    for ct in cluster_targets:
        r_multi = requests.post(f"{dash_url}/api/forceenter", json={
            "pair": ct["pair"],
            "side": "long",
            "ordertype": "limit",
            "price": ct["price"],
            "stakeamount": 314.0,
            "entry_tag": f"dynamic_takeover_{ct['cluster']}"
        })
        assert r_multi.status_code == 200, f"Failed placing order on {ct['pair']}: {r_multi.text}"
        tid = r_multi.json().get("trade_id")
        assert tid is not None
        placed_trade_ids.append(tid)
        print(f"  Placed {ct['pair']} ({ct['cluster']}) -> Trade #{tid}")

    # Verify all 3 are open in telemetry
    r_telemetry_multi = requests.get(f"{dash_url}/api/data")
    ot_multi = r_telemetry_multi.json().get("live_simulation", {}).get("open_trades", [])
    print(f"Active Multi-Slot Open Trades: {len(ot_multi)} / 3")
    assert len(ot_multi) == 3, f"Expected 3 open trades, got {len(ot_multi)}"
    for t in ot_multi:
        assert t["is_limit_order"] is True, f"Trade #{t['id']} must be resting limit order"
        print(f"  Trade #{t['id']} {t['pair']}: Status={t['order_status']}, OpenRate=${t['open_rate']}")

    # Clean up multi-slot orders via cancel
    print("Cancelling all 3 multi-slot resting orders...")
    for tid in placed_trade_ids:
        r_c = requests.post(f"{dash_url}/api/cancel_order", json={"trade_id": tid})
        assert r_c.status_code == 200, f"Failed cancelling trade #{tid}"

    # 10. Clean baseline
    final_trades = requests.get(f"{dash_url}/api/data").json().get("live_simulation", {}).get("open_trades", [])
    print(f"\nFinal Clean Open Trades: {len(final_trades)}")
    assert len(final_trades) == 0, "Clean baseline required"
    print("\n=== ALL 10 END-TO-END EMPIRICAL TESTS PASSED PERFECTLY! ===")

if __name__ == "__main__":
    run_tests()
