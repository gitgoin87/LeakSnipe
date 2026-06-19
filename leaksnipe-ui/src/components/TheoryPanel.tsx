import { useCallback, useEffect, useState } from "react";
import {
  api,
  waitForBackend,
  type CfrResult,
  type ChartCell,
  type TheoryChartResult,
  type TheoryGame,
  type ValueNetResult,
} from "../lib/api";

const DEPTHS = [5, 10, 25, 35, 50, 75, 100] as const;
const POSITIONS = ["UTG", "MP", "CO", "BTN", "SB", "BB"] as const;
const RANKS = "AKQJT98765432";

const ACTION_COLORS: Record<string, string> = {
  fold: "var(--chart-fold, #334155)",
  push: "var(--chart-push, #ef4444)",
  open: "var(--chart-open, #22c55e)",
  call: "var(--chart-call, #3b82f6)",
  defend: "var(--chart-defend, #14b8a6)",
  "3bet": "var(--chart-3bet, #a855f7)",
};

function cellLabel(cell: ChartCell | null): string {
  if (!cell) return "";
  const pct = Math.round(cell.freq * 100);
  if (pct >= 95) return cell.notation;
  return `${cell.notation} ${pct}%`;
}

function RangeGrid({
  grid,
  selected,
  onSelect,
}: {
  grid: (ChartCell | null)[][];
  selected: string | null;
  onSelect: (notation: string, cell: ChartCell) => void;
}) {
  return (
    <div className="range-chart-wrap">
      <table className="range-chart">
        <thead>
          <tr>
            <th />
            {RANKS.split("").map((r) => (
              <th key={r}>{r}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, ri) => (
            <tr key={RANKS[ri]}>
              <th>{RANKS[ri]}</th>
              {row.map((cell, ci) => {
                if (!cell) {
                  return <td key={ci} className="range-cell empty" />;
                }
                const isSel = selected === cell.notation;
                return (
                  <td
                    key={ci}
                    className={`range-cell action-${cell.action}${isSel ? " selected" : ""}`}
                    style={{ backgroundColor: ACTION_COLORS[cell.action] ?? ACTION_COLORS.fold }}
                    title={`${cell.notation}: ${cell.action} ${(cell.freq * 100).toFixed(0)}%${cell.nn_value_pct != null ? ` · NN ${cell.nn_value_pct.toFixed(0)}%` : ""}`}
                    onClick={() => onSelect(cell.notation, cell)}
                  >
                    <span className="range-cell-text">{cellLabel(cell)}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TheoryPanel() {
  const [games, setGames] = useState<TheoryGame[]>([]);
  const [depths, setDepths] = useState<number[]>([...DEPTHS]);
  const [stackBb, setStackBb] = useState<number>(25);
  const [position, setPosition] = useState<string>("BTN");
  const [antePerPlayer, setAntePerPlayer] = useState(500);
  const [numPlayers, setNumPlayers] = useState(9);

  const [chart, setChart] = useState<TheoryChartResult | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [selectedHand, setSelectedHand] = useState<string | null>(null);
  const [selectedCell, setSelectedCell] = useState<ChartCell | null>(null);

  const [gameId, setGameId] = useState("tournament_push_fold");
  const [iterations, setIterations] = useState(10000);
  const [cfrLoading, setCfrLoading] = useState(false);
  const [cfrResult, setCfrResult] = useState<CfrResult | null>(null);

  const [hero, setHero] = useState("AsKh");
  const [board, setBoard] = useState("");
  const [potOdds, setPotOdds] = useState(0.33);
  const [valueLoading, setValueLoading] = useState(false);
  const [valueResult, setValueResult] = useState<ValueNetResult | null>(null);
  const [trainLoading, setTrainLoading] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const deadMoney = antePerPlayer * numPlayers;

  const loadChart = useCallback(async () => {
    setChartLoading(true);
    setError(null);
    try {
      await waitForBackend();
      const res = await api.theoryChart({
        stack_bb: stackBb,
        position,
        ante_per_player: antePerPlayer,
        num_players: numPlayers,
        include_nn: true,
      });
      setChart(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setChartLoading(false);
    }
  }, [stackBb, position, antePerPlayer, numPlayers]);

  useEffect(() => {
    void (async () => {
      try {
        await waitForBackend();
        const [g, d] = await Promise.all([api.theoryGames(), api.theoryDepths()]);
        setGames(g.games);
        if (d.depths.length) setDepths(d.depths);
      } catch {
        // optional
      }
    })();
  }, []);

  useEffect(() => {
    void loadChart();
  }, [loadChart]);

  const runCfr = async () => {
    setCfrLoading(true);
    setError(null);
    try {
      await waitForBackend();
      setCfrResult(
        await api.theoryCfr({
          game: gameId,
          iterations,
          ante_per_player: gameId === "tournament_push_fold" ? antePerPlayer : 0,
          num_players: gameId === "tournament_push_fold" ? numPlayers : 2,
          stack_bb: stackBb,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCfrLoading(false);
    }
  };

  const runValue = async () => {
    setValueLoading(true);
    setError(null);
    try {
      await waitForBackend();
      const posIdx = POSITIONS.indexOf(position as (typeof POSITIONS)[number]) / (POSITIONS.length - 1);
      setValueResult(
        await api.theoryValue({
          hero,
          board,
          pot_odds: potOdds,
          position: posIdx,
          ante_per_player: antePerPlayer,
          dead_money: deadMoney,
          stack_bb: stackBb,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setValueLoading(false);
    }
  };

  const trainNet = async () => {
    setTrainLoading(true);
    setTrainMsg(null);
    setError(null);
    try {
      await waitForBackend();
      const res = await api.theoryValueTrain({ n_samples: 200, epochs: 50 });
      setTrainMsg(
        `Trained (${res.backend}): val MAE ${res.val_mae?.toFixed(3) ?? "—"} — saved to ${res.path}`,
      );
      void loadChart();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTrainLoading(false);
    }
  };

  const onCellSelect = (notation: string, cell: ChartCell) => {
    setSelectedHand(notation);
    setSelectedCell(cell);
    if (notation.length === 2) {
      setHero(`${notation[0]}s${notation[1]}h`);
    } else if (notation.endsWith("s")) {
      setHero(`${notation[0]}s${notation[1]}s`);
    } else {
      setHero(`${notation[0]}s${notation[1]}h`);
    }
  };

  const selected = games.find((g) => g.id === gameId);

  return (
    <div className="theory-panel">
      <div className="theory-disclaimer panel-card">
        <h3>Unified theory engine</h3>
        <p>
          CFR+ calibrates push/fold at each stack depth; charts map those frequencies to a 13×13 grid.
          The neural value net estimates equity/EV with stack + ante features. Educational tooling —
          not a full NLHE solver.
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <section className="panel-card theory-config">
        <h3>MTT config</h3>
        <div className="theory-config-row">
          <label>
            Ante / player
            <input
              type="number"
              min={0}
              step={50}
              value={antePerPlayer}
              onChange={(e) => setAntePerPlayer(Number(e.target.value))}
            />
          </label>
          <label>
            Table size
            <input
              type="number"
              min={2}
              max={10}
              value={numPlayers}
              onChange={(e) => setNumPlayers(Number(e.target.value))}
            />
          </label>
          <div className="theory-meta">
            Dead money: <strong>{deadMoney}</strong> chips · pot base ≈{" "}
            <strong>{chart?.pot_base_bb?.toFixed(1) ?? "—"}</strong> bb
          </div>
        </div>
      </section>

      <section className="panel-card theory-charts">
        <div className="theory-chart-header">
          <h3>Stack-depth chart</h3>
          <div className="depth-tabs">
            {depths.map((d) => (
              <button
                key={d}
                type="button"
                className={`depth-tab${stackBb === d ? " active" : ""}`}
                onClick={() => setStackBb(d)}
              >
                {d}BB
              </button>
            ))}
          </div>
        </div>
        <label>
          Position
          <select value={position} onChange={(e) => setPosition(e.target.value)}>
            {POSITIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        {chartLoading && <p className="muted">Loading chart…</p>}
        {chart && !chartLoading && (
          <>
            <div className="chart-legend">
              {chart.legend.map((a) => (
                <span key={a} className="legend-item">
                  <span className="legend-swatch" style={{ background: ACTION_COLORS[a] }} />
                  {a}
                </span>
              ))}
            </div>
            <RangeGrid
              grid={chart.grid}
              selected={selectedHand}
              onSelect={onCellSelect}
            />
            <div className="theory-results compact">
              <div className="stat-row">
                <span>Mode / source</span>
                <strong>
                  {chart.mode} · {chart.source}
                </strong>
              </div>
              <div className="stat-row">
                <span>CFR+ exploitability</span>
                <strong>{chart.cfr.exploitability?.toFixed(4) ?? "—"}</strong>
              </div>
              {selectedCell && (
                <div className="stat-row">
                  <span>{selectedHand}</span>
                  <strong>
                    {selectedCell.action} {(selectedCell.freq * 100).toFixed(0)}%
                    {selectedCell.nn_value_pct != null && ` · NN ${selectedCell.nn_value_pct.toFixed(0)}%`}
                  </strong>
                </div>
              )}
            </div>
            <p className="muted small">{chart.note}</p>
          </>
        )}
      </section>

      <div className="theory-grid">
        <section className="panel-card theory-cfr">
          <h3>CFR+ solver</h3>
          <p className="muted">{selected?.description ?? "Tournament push/fold with antes"}</p>
          <label>
            Subgame
            <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
              {games.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
              {!games.length && <option value="tournament_push_fold">MTT Push/Fold</option>}
            </select>
          </label>
          <label>
            Iterations
            <input
              type="number"
              min={100}
              max={500000}
              value={iterations}
              onChange={(e) => setIterations(Number(e.target.value))}
            />
          </label>
          <p className="muted small">
            Uses chart depth <strong>{stackBb}BB</strong> and ante config above.
          </p>
          <button type="button" className="primary-btn" disabled={cfrLoading} onClick={() => void runCfr()}>
            {cfrLoading ? "Running CFR+…" : "Run CFR+"}
          </button>
          {cfrResult && (
            <div className="theory-results">
              <div className="stat-row">
                <span>Exploitability</span>
                <strong>{cfrResult.exploitability?.toFixed(4) ?? "—"}</strong>
              </div>
              <div className="stat-row">
                <span>EV (P0 / P1)</span>
                <strong>
                  {cfrResult.ev?.player_0?.toFixed(3)} / {cfrResult.ev?.player_1?.toFixed(3)}
                </strong>
              </div>
              {cfrResult.config && (
                <div className="stat-row">
                  <span>Pot base / dead</span>
                  <strong>
                    {cfrResult.config.pot_base_bb?.toFixed(1)}bb · {cfrResult.config.dead_money?.toFixed(0)} chips
                  </strong>
                </div>
              )}
              <h4>Strategy (avg)</h4>
              <div className="strategy-table-wrap">
                <table className="strategy-table">
                  <thead>
                    <tr>
                      <th>Info set</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(cfrResult.strategy ?? {}).map(([iset, acts]) => (
                      <tr key={iset}>
                        <td>{iset}</td>
                        <td>
                          {Object.entries(acts)
                            .map(([a, p]) => `${a} ${(p * 100).toFixed(1)}%`)
                            .join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        <section className="panel-card theory-value">
          <h3>Neural value @ depth</h3>
          <p className="muted">
            MLP trained on MC equity with stack_bb + ante features. Click a chart cell or enter cards.
          </p>
          <label>
            Hero
            <input value={hero} onChange={(e) => setHero(e.target.value)} placeholder="AsKh" />
          </label>
          <label>
            Board
            <input value={board} onChange={(e) => setBoard(e.target.value)} placeholder="optional" />
          </label>
          <label>
            Pot odds (0–1)
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={potOdds}
              onChange={(e) => setPotOdds(Number(e.target.value))}
            />
          </label>
          <p className="muted small">
            Depth <strong>{stackBb}BB</strong> · {position} · ante {antePerPlayer}
          </p>
          <div className="btn-row">
            <button type="button" className="primary-btn" disabled={valueLoading} onClick={() => void runValue()}>
              {valueLoading ? "Predicting…" : "Predict value"}
            </button>
            <button type="button" className="ghost-btn" disabled={trainLoading} onClick={() => void trainNet()}>
              {trainLoading ? "Training…" : "Train model"}
            </button>
          </div>
          {valueResult && (
            <div className="theory-results">
              <div className="stat-row big">
                <span>Value @ {stackBb}BB</span>
                <strong className="equity-highlight">{valueResult.value_pct?.toFixed(1)}%</strong>
              </div>
              <p className="muted small">
                Source: {valueResult.source} — {valueResult.note}
              </p>
            </div>
          )}
          {trainMsg && <p className="success-text">{trainMsg}</p>}
        </section>
      </div>
    </div>
  );
}
