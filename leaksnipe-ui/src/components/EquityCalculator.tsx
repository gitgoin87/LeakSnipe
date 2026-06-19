import { useEffect, useState } from "react";
import {
  api,
  waitForBackend,
  type EquityRangesResult,
  type EquityResult,
  type Omaha8Result,
} from "../lib/api";

type Game = "nlhe" | "omaha8" | "stud" | "stud8";
type VillainMode = "position" | "range" | "hand";
type StudVillainMode = "random" | "hand" | "range";

const GAMES: { id: Game; label: string }[] = [
  { id: "nlhe", label: "Hold'em (NLHE)" },
  { id: "omaha8", label: "Omaha Hi/Lo" },
  { id: "stud", label: "7-Card Stud" },
  { id: "stud8", label: "Stud Hi/Lo" },
];

const POSITIONS = ["UTG", "MP", "HJ", "CO", "BTN", "SB", "BB"];
const ACTIONS = [
  { id: "open", label: "Open / RFI" },
  { id: "steal", label: "Steal" },
  { id: "3bet", label: "3-bet" },
  { id: "defend", label: "BB defend vs steal" },
];

function pct(value?: number): string {
  return value === undefined || value === null ? "—" : `${value.toFixed(1)}%`;
}

function calcPotOdds(pot: number, toCall: number): number | null {
  if (toCall <= 0 || pot + toCall <= 0) return null;
  return (toCall / (pot + toCall)) * 100;
}

export function EquityCalculator() {
  const [game, setGame] = useState<Game>("nlhe");

  // NLHE
  const [hero, setHero] = useState("Kh2d");
  const [board, setBoard] = useState("");
  const [potOddsPot, setPotOddsPot] = useState("250");
  const [potOddsCall, setPotOddsCall] = useState("50");
  const [potOddsCallers, setPotOddsCallers] = useState("2");
  const [villainMode, setVillainMode] = useState<VillainMode>("position");
  const [villainPosition, setVillainPosition] = useState("BTN");
  const [actionContext, setActionContext] = useState("open");
  const [villainRange, setVillainRange] = useState("22+, A2s+, KQo");
  const [villainHand, setVillainHand] = useState("AsKs");

  // Omaha Hi/Lo
  const [omahaHero, setOmahaHero] = useState("As2sKsQh");
  const [omahaOpponents, setOmahaOpponents] = useState(1);

  // Seven Card Stud (high)
  const [studHero, setStudHero] = useState("AhKhQhJh2c");
  const [studVillainMode, setStudVillainMode] = useState<StudVillainMode>("hand");
  const [studVillainHand, setStudVillainHand] = useState("TsTd");
  const [studVillainRange, setStudVillainRange] = useState("22+, AKs");
  const [studOpponents, setStudOpponents] = useState(1);

  // Stud Hi/Lo
  const [stud8Hero, setStud8Hero] = useState("Ah2s3d4c5h");
  const [stud8Opponents, setStud8Opponents] = useState(1);

  // Shared by both stud variants — known/visible upcards + folded cards.
  const [deadCards, setDeadCards] = useState("");

  const [iters, setIters] = useState(12000);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nlheResult, setNlheResult] = useState<EquityResult | null>(null);
  const [splitResult, setSplitResult] = useState<Omaha8Result | null>(null);
  const [ranges, setRanges] = useState<EquityRangesResult | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        await waitForBackend();
        setRanges(await api.equityRanges());
      } catch {
        // ranges are optional reference info
      }
    })();
  }, []);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      await waitForBackend();
      setNlheResult(null);
      setSplitResult(null);
      if (game === "nlhe") {
        const body: Parameters<typeof api.equity>[0] = { hero, board, iters };
        if (villainMode === "hand") body.villain_hand = villainHand;
        else if (villainMode === "range") body.villain_range = villainRange;
        else {
          body.villain_position = villainPosition;
          body.action_context = actionContext;
        }
        setNlheResult(await api.equity(body));
      } else if (game === "omaha8") {
        setSplitResult(
          await api.equityOmaha8({ hero: omahaHero, opponents: omahaOpponents, board, iters }),
        );
      } else if (game === "stud") {
        const body: Parameters<typeof api.equityStud>[0] = {
          hero: studHero,
          dead_cards: deadCards,
          iters,
        };
        if (studVillainMode === "hand") body.villain_hand = studVillainHand;
        else if (studVillainMode === "range") body.villain_range = studVillainRange;
        else body.opponents = studOpponents;
        setNlheResult(await api.equityStud(body));
      } else {
        setSplitResult(
          await api.equityStud8({
            hero: stud8Hero,
            opponents: stud8Opponents,
            dead_cards: deadCards,
            iters,
          }),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="equity-panel">
      <div className="equity-game-toggle">
        {GAMES.map((g) => (
          <button
            key={g.id}
            type="button"
            className={`tab-button ${game === g.id ? "active" : ""}`}
            onClick={() => setGame(g.id)}
          >
            {g.label}
          </button>
        ))}
      </div>

      {game === "nlhe" ? (
        <div className="form-grid">
          <label className="form-field">
            <span>Hero hole cards</span>
            <input value={hero} onChange={(e) => setHero(e.target.value)} placeholder="Kh2d" />
          </label>
          <label className="form-field">
            <span>Board (optional)</span>
            <input value={board} onChange={(e) => setBoard(e.target.value)} placeholder="As Kd 2c" />
          </label>
          <label className="form-field">
            <span>Villain type</span>
            <select value={villainMode} onChange={(e) => setVillainMode(e.target.value as VillainMode)}>
              <option value="position">Position range (Nash approx)</option>
              <option value="range">Custom range</option>
              <option value="hand">Specific hand</option>
            </select>
          </label>
          {villainMode === "position" ? (
            <>
              <label className="form-field">
                <span>Villain position</span>
                <select value={villainPosition} onChange={(e) => setVillainPosition(e.target.value)}>
                  {POSITIONS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
              <label className="form-field">
                <span>Action</span>
                <select value={actionContext} onChange={(e) => setActionContext(e.target.value)}>
                  {ACTIONS.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
            </>
          ) : null}
          {villainMode === "range" ? (
            <label className="form-field" style={{ gridColumn: "1 / -1" }}>
              <span>Villain range</span>
              <input
                value={villainRange}
                onChange={(e) => setVillainRange(e.target.value)}
                placeholder="22+, A2s+, KTs+, QJs, AKo  (or 'top 15%')"
              />
            </label>
          ) : null}
          {villainMode === "hand" ? (
            <label className="form-field">
              <span>Villain hand</span>
              <input value={villainHand} onChange={(e) => setVillainHand(e.target.value)} placeholder="AsKs" />
            </label>
          ) : null}
          <details className="equity-ranges">
            <summary>Pot odds (multi-way)</summary>
            <div className="form-grid">
              <label className="form-field">
                <span>Pot before your call (incl. callers)</span>
                <input
                  value={potOddsPot}
                  onChange={(e) => setPotOddsPot(e.target.value)}
                  placeholder="250"
                />
              </label>
              <label className="form-field">
                <span>To call</span>
                <input
                  value={potOddsCall}
                  onChange={(e) => setPotOddsCall(e.target.value)}
                  placeholder="50"
                />
              </label>
              <label className="form-field">
                <span>Callers already in (count)</span>
                <input
                  type="number"
                  min={0}
                  max={8}
                  value={potOddsCallers}
                  onChange={(e) => setPotOddsCallers(e.target.value)}
                />
              </label>
            </div>
            {(() => {
              const pot = Number(potOddsPot) || 0;
              const tc = Number(potOddsCall) || 0;
              const odds = calcPotOdds(pot, tc);
              const callers = Math.max(0, Number(potOddsCallers) || 0);
              const naive = callers > 0 ? calcPotOdds(Math.max(0, pot - callers * tc), tc) : null;
              return odds != null ? (
                <p className="field-hint">
                  Pot odds: {odds.toFixed(1)}%
                  {callers > 0 ? ` (${callers + 1}-way with you)` : ""}
                  {naive != null && naive > odds + 0.05 ? (
                    <> — not {naive.toFixed(1)}% if callers&apos; chips were ignored</>
                  ) : null}
                </p>
              ) : null;
            })()}
          </details>
        </div>
      ) : null}

      {game === "omaha8" ? (
        <div className="form-grid">
          <label className="form-field">
            <span>Hero hole cards (4)</span>
            <input value={omahaHero} onChange={(e) => setOmahaHero(e.target.value)} placeholder="As2sKsQh" />
          </label>
          <label className="form-field">
            <span>Board (optional)</span>
            <input value={board} onChange={(e) => setBoard(e.target.value)} placeholder="3h 4d 5c" />
          </label>
          <label className="form-field">
            <span>Opponents (random)</span>
            <input
              type="number"
              min={1}
              max={8}
              value={omahaOpponents}
              onChange={(e) => setOmahaOpponents(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
            />
          </label>
        </div>
      ) : null}

      {game === "stud" ? (
        <div className="form-grid">
          <label className="form-field">
            <span>Hero cards (up to 7)</span>
            <input value={studHero} onChange={(e) => setStudHero(e.target.value)} placeholder="AhKhQhJh2c" />
          </label>
          <label className="form-field">
            <span>Villain type</span>
            <select
              value={studVillainMode}
              onChange={(e) => setStudVillainMode(e.target.value as StudVillainMode)}
            >
              <option value="hand">Specific hand</option>
              <option value="range">Range (key cards)</option>
              <option value="random">Random opponents</option>
            </select>
          </label>
          {studVillainMode === "hand" ? (
            <label className="form-field">
              <span>Villain cards</span>
              <input value={studVillainHand} onChange={(e) => setStudVillainHand(e.target.value)} placeholder="TsTd" />
            </label>
          ) : null}
          {studVillainMode === "range" ? (
            <label className="form-field">
              <span>Villain range</span>
              <input value={studVillainRange} onChange={(e) => setStudVillainRange(e.target.value)} placeholder="22+, AKs" />
            </label>
          ) : null}
          {studVillainMode === "random" ? (
            <label className="form-field">
              <span>Opponents</span>
              <input
                type="number"
                min={1}
                max={6}
                value={studOpponents}
                onChange={(e) => setStudOpponents(Math.max(1, Math.min(6, Number(e.target.value) || 1)))}
              />
            </label>
          ) : null}
          <label className="form-field" style={{ gridColumn: "1 / -1" }}>
            <span>Dead / visible upcards (removed from deck)</span>
            <input value={deadCards} onChange={(e) => setDeadCards(e.target.value)} placeholder="Ts 9c 9d (opponents' upcards, folded cards)" />
          </label>
        </div>
      ) : null}

      {game === "stud8" ? (
        <div className="form-grid">
          <label className="form-field">
            <span>Hero cards (up to 7)</span>
            <input value={stud8Hero} onChange={(e) => setStud8Hero(e.target.value)} placeholder="Ah2s3d4c5h" />
          </label>
          <label className="form-field">
            <span>Opponents (random)</span>
            <input
              type="number"
              min={1}
              max={6}
              value={stud8Opponents}
              onChange={(e) => setStud8Opponents(Math.max(1, Math.min(6, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="form-field" style={{ gridColumn: "1 / -1" }}>
            <span>Dead / visible upcards (removed from deck)</span>
            <input value={deadCards} onChange={(e) => setDeadCards(e.target.value)} placeholder="Ks Qd 7h (opponents' upcards, folded cards)" />
          </label>
        </div>
      ) : null}

      <div className="equity-actions">
        <label className="form-field inline">
          <span>Iterations</span>
          <input
            type="number"
            min={200}
            max={200000}
            step={1000}
            value={iters}
            onChange={(e) => setIters(Math.max(200, Math.min(200000, Number(e.target.value) || 12000)))}
          />
        </label>
        <button type="button" className="primary-btn" onClick={() => void run()} disabled={loading}>
          {loading ? "Computing…" : "Compute equity"}
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {nlheResult ? (
        <div className="equity-results">
          <div className="card-grid">
            <div className="stat-card accent">
              <div className="stat-label">Hero equity</div>
              <div className="stat-value">{pct(nlheResult.hero_equity ?? nlheResult.equity?.[0])}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Win</div>
              <div className="stat-value">{pct(nlheResult.hero_win)}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Tie</div>
              <div className="stat-value">{pct(nlheResult.hero_tie)}</div>
            </div>
          </div>
          {nlheResult.villain_range ? (
            <p className="field-hint">
              Villain range: <code>{nlheResult.villain_range}</code>
              {nlheResult.villain_range_pct !== undefined
                ? ` (${nlheResult.villain_range_pct}% of hands)`
                : ""}
            </p>
          ) : null}
          {nlheResult.rows ? (
            <table className="equity-table">
              <thead>
                <tr>
                  <th>Vs</th>
                  <th>Equity</th>
                </tr>
              </thead>
              <tbody>
                {nlheResult.rows.map((row) => (
                  <tr key={row.label}>
                    <td>{row.label}</td>
                    <td>{pct(row.equity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <p className="field-hint">{nlheResult.iterations?.toLocaleString()} Monte Carlo trials.</p>
        </div>
      ) : null}

      {splitResult ? (
        <div className="equity-results">
          <div className="card-grid">
            <div className="stat-card accent">
              <div className="stat-label">Overall equity</div>
              <div className="stat-value">{pct(splitResult.overall_equity)}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">High equity</div>
              <div className="stat-value">{pct(splitResult.high_equity)}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Low equity</div>
              <div className="stat-value">{pct(splitResult.low_equity)}</div>
            </div>
            <div className="stat-card accent-gold">
              <div className="stat-label">Scoop equity</div>
              <div className="stat-value">{pct(splitResult.scoop_equity)}</div>
            </div>
          </div>
          <p className="field-hint">
            Low qualifies {pct(splitResult.low_possible_pct)} of runouts · {splitResult.players} players ·{" "}
            {splitResult.iterations?.toLocaleString()} trials.
          </p>
        </div>
      ) : null}

      {ranges && game === "nlhe" ? (
        <details className="equity-ranges">
          <summary>Reference ranges (solver approximations)</summary>
          <p className="field-hint">{ranges.note}</p>
          <table className="equity-table">
            <thead>
              <tr>
                <th>Position (RFI)</th>
                <th>Freq</th>
                <th>Range</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(ranges.rfi).map(([pos, entry]) => (
                <tr key={pos}>
                  <td>{pos}</td>
                  <td>{entry.pct}%</td>
                  <td className="mono small">{entry.range}</td>
                </tr>
              ))}
              <tr>
                <td>BB defend</td>
                <td>{ranges.bb_defend_vs_steal.pct}%</td>
                <td className="mono small">{ranges.bb_defend_vs_steal.range}</td>
              </tr>
            </tbody>
          </table>
        </details>
      ) : null}
    </div>
  );
}
