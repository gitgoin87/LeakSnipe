// Multi-Client Hand History Parser
// Supports: WPN/ACR (BetACR), CoinPoker, and DriveHUD2 formats
// Hold'em and Omaha variants

export interface ParsedPlayer {
  name: string;
  seat: number;
  chips: number;
  cards?: string[];
  isDealer?: boolean;
  isHero?: boolean;
}

export interface ParsedAction {
  playerName: string;
  playerSeat: number;
  type: 'post' | 'fold' | 'check' | 'call' | 'bet' | 'raise' | 'allin' | 'win' | 'show';
  amount?: number;
  street: 'Preflop' | 'Flop' | 'Turn' | 'River' | 'Showdown';
}

export interface ParsedHand {
  id: string;
  site: string;
  gameType: string; // 'NLHE', 'PLO', 'PLO5', etc.
  stakes: string;
  tableName: string;
  timestamp: string;
  players: ParsedPlayer[];
  actions: ParsedAction[];
  board: string[];
  heroCards: string[];
  heroName: string;
  potSize: number;
  heroWon: boolean;
  heroNetAmount: number;
  dealerSeat: number;
  raw: string;
}

// ── Public entry point ──────────────────────────────────────────────

export function parseHandHistory(raw: string, defaultSite: string = 'DriveHUD2'): ParsedHand[] {
  const hands: ParsedHand[] = [];

  // Split by hand boundaries for ALL supported formats
  const handTexts = raw.split(
    /(?=(?:Winning Poker Network|Game Hand #|Hand #|PokerStars Hand|CoinPoker Hand #|Ignition Hand #))/gi
  ).filter(t => t.trim().length > 50);

  for (const text of handTexts) {
    try {
      const trimmed = text.trim();
      const site = detectSite(trimmed, defaultSite);
      let hand: ParsedHand | null = null;

      if (site === 'CoinPoker') {
        hand = parseCoinPokerHand(trimmed);
      } else {
        // WPN / ACR / DriveHUD2 / generic
        hand = parseWPNHand(trimmed, site);
      }

      if (hand) hands.push(hand);
    } catch (_e) {
      // Skip unparseable hands silently
    }
  }
  return hands;
}

// ── Site detection ──────────────────────────────────────────────────

function detectSite(text: string, fallback: string): string {
  if (/CoinPoker Hand/i.test(text)) return 'CoinPoker';
  if (/Winning Poker Network/i.test(text) || /WPN/i.test(text)) return 'BetACR';
  if (/Americas Cardroom/i.test(text) || /ACR/i.test(text)) return 'BetACR';
  if (/BetOnline/i.test(text)) return 'BetACR';
  if (/Ignition/i.test(text) || /Bovada/i.test(text)) return 'Ignition';
  if (/PokerStars/i.test(text)) return 'PokerStars';
  return fallback;
}

// ── Shared helpers ──────────────────────────────────────────────────

function detectGameType(text: string): string {
  if (/Omaha Hi[/-]?Lo/i.test(text)) return 'PLO8';
  if (/5[- ]?Card (PLO|Omaha)/i.test(text) || /PLO5/i.test(text)) return 'PLO5';
  if (/Pot Limit Omaha/i.test(text) || /\bPLO\b/i.test(text) || /Omaha/i.test(text)) return 'PLO';
  if (/No Limit Hold/i.test(text) || /NL Hold/i.test(text)) return 'NLHE';
  if (/Pot Limit Hold/i.test(text)) return 'PLHE';
  if (/Limit Hold/i.test(text)) return 'LHE';
  return 'NLHE';
}

function parseStakes(text: string): string {
  // Match $0.02/$0.04, 0.10/0.25, $1/$2 etc.
  const m = text.match(/\$?([\d,.]+)\s*\/\s*\$?([\d,.]+)/);
  return m ? `${m[1].replace(/,/g, '')}/${m[2].replace(/,/g, '')}` : 'unknown';
}

function parseBoardCards(text: string): string[] {
  let board: string[] = [];
  const flopMatch = text.match(/\*{3}\s*FLOP\s*\*{3}\s*\[([^\]]+)\]/i);
  const turnMatch = text.match(/\*{3}\s*TURN\s*\*{3}\s*\[[^\]]+\]\s*\[([^\]]+)\]/i);
  const riverMatch = text.match(/\*{3}\s*RIVER\s*\*{3}\s*\[[^\]]+\]\s*\[([^\]]+)\]/i);

  if (flopMatch) board = flopMatch[1].trim().split(/\s+/);
  if (turnMatch) board.push(...turnMatch[1].trim().split(/\s+/));
  if (riverMatch) board.push(...riverMatch[1].trim().split(/\s+/));

  // Fallback: Board: [Ah Kd 2c 7s Jh]
  if (board.length === 0) {
    const boardMatch = text.match(/Board:\s*\[([^\]]+)\]/i);
    if (boardMatch) board = boardMatch[1].trim().split(/\s+/);
  }
  return board;
}

function parseActions(lines: string[], players: ParsedPlayer[]): ParsedAction[] {
  const actions: ParsedAction[] = [];
  let currentStreet: ParsedAction['street'] = 'Preflop';

  for (const line of lines) {
    // Street markers
    if (/\*{3}\s*FLOP/i.test(line)) { currentStreet = 'Flop'; continue; }
    if (/\*{3}\s*TURN/i.test(line)) { currentStreet = 'Turn'; continue; }
    if (/\*{3}\s*RIVER/i.test(line)) { currentStreet = 'River'; continue; }
    if (/\*{3}\s*SHOW\s*DOWN/i.test(line) || /\*{3}\s*SUMMARY/i.test(line)) { currentStreet = 'Showdown'; continue; }

    // Post blinds or antes
    const postMatch = line.match(/^(.+?)(?::\s*|\s+)posts\s+(?:.*?)\$?([\d,.]+)/i);
    if (postMatch) {
      const player = players.find(p => p.name === postMatch[1].trim());
      actions.push({ playerName: postMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'post', amount: parseFloat(postMatch[2].replace(/,/g, '')), street: 'Preflop' });
      continue;
    }

    // Folds
    const foldMatch = line.match(/^(.+?)(?::\s*|\s+)folds/i);
    if (foldMatch) {
      const player = players.find(p => p.name === foldMatch[1].trim());
      actions.push({ playerName: foldMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'fold', street: currentStreet });
      continue;
    }

    // Checks
    const checkMatch = line.match(/^(.+?)(?::\s*|\s+)checks/i);
    if (checkMatch) {
      const player = players.find(p => p.name === checkMatch[1].trim());
      actions.push({ playerName: checkMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'check', street: currentStreet });
      continue;
    }

    // Calls
    const callMatch = line.match(/^(.+?)(?::\s*|\s+)calls\s+\$?([\d,.]+)/i);
    if (callMatch) {
      const player = players.find(p => p.name === callMatch[1].trim());
      actions.push({ playerName: callMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'call', amount: parseFloat(callMatch[2].replace(/,/g, '')), street: currentStreet });
      continue;
    }

    // Bets
    const betMatch = line.match(/^(.+?)(?::\s*|\s+)bets\s+\$?([\d,.]+)/i);
    if (betMatch) {
      const player = players.find(p => p.name === betMatch[1].trim());
      actions.push({ playerName: betMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'bet', amount: parseFloat(betMatch[2].replace(/,/g, '')), street: currentStreet });
      continue;
    }

    // Raises
    const raiseMatch = line.match(/^(.+?)(?::\s*|\s+)raises\s+\$?([\d,.]+)(?:\s+to\s+\$?([\d,.]+))?/i);
    if (raiseMatch) {
      const player = players.find(p => p.name === raiseMatch[1].trim());
      const amount = raiseMatch[3] ? parseFloat(raiseMatch[3].replace(/,/g, '')) : parseFloat(raiseMatch[2].replace(/,/g, ''));
      actions.push({ playerName: raiseMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'raise', amount, street: currentStreet });
      continue;
    }

    // All-in (modify previous action)
    if (/all-in/i.test(line) && actions.length > 0) {
      const lastAction = actions[actions.length - 1];
      if (lastAction) lastAction.type = 'allin';
    }

    // Wins pot
    const winMatch = line.match(/^(.+?)\s+(?:collected|wins|won)\s+\$?([\d,.]+)/i);
    if (winMatch) {
      const player = players.find(p => p.name === winMatch[1].trim());
      actions.push({ playerName: winMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'win', amount: parseFloat(winMatch[2].replace(/,/g, '')), street: 'Showdown' });
      continue;
    }

    // Shows
    const showMatch = line.match(/^(.+?)(?::\s*|\s+)shows\s+\[([^\]]+)\]/i);
    if (showMatch) {
      const player = players.find(p => p.name === showMatch[1].trim());
      if (player) player.cards = showMatch[2].trim().split(/\s+/);
      actions.push({ playerName: showMatch[1].trim(), playerSeat: player?.seat || 0,
        type: 'show', street: 'Showdown' });
    }
  }

  return actions;
}

function calcHeroResult(actions: ParsedAction[], heroName: string) {
  const potSize = actions
    .filter(a => ['post', 'call', 'bet', 'raise', 'allin'].includes(a.type))
    .reduce((sum, a) => sum + (a.amount || 0), 0);

  const heroWinAction = actions.find(a => a.type === 'win' && a.playerName === heroName);
  const heroWon = !!heroWinAction;
  const heroBets = actions
    .filter(a => a.playerName === heroName && ['post', 'call', 'bet', 'raise', 'allin'].includes(a.type))
    .reduce((sum, a) => sum + (a.amount || 0), 0);
  const heroNetAmount = heroWon ? (heroWinAction!.amount! - heroBets) : -heroBets;

  return { potSize, heroWon, heroNetAmount };
}

// ── CoinPoker parser ────────────────────────────────────────────────
// CoinPoker uses a PokerStars-like text format:
//   CoinPoker Hand #551234567:  Hold'em No Limit ($0.02/$0.04 USD) - 2024/01/15 12:30:45 UTC
//   Table 'NL 0.02-0.04 (6-max)' 6-max Seat #3 is the button
//   Seat 1: Player1 ($4.00 in chips)
//   *** HOLE CARDS ***   Dealt to Hero [Ah Kd]
//   *** FLOP *** [Jh 7s 2c]
//   *** SUMMARY ***

function parseCoinPokerHand(text: string): ParsedHand | null {
  const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
  if (lines.length < 5) return null;

  // Hand ID:  CoinPoker Hand #551234567
  const idMatch = text.match(/CoinPoker Hand #(\d+)/i);
  const handId = idMatch ? `CP-${idMatch[1]}` : `CP-${Date.now()}`;

  const gameType = detectGameType(text);
  const stakes = parseStakes(text);

  // Table name
  const tableMatch = text.match(/Table\s+'([^']+)'/i) || text.match(/Table\s+"([^"]+)"/i);
  const tableName = tableMatch ? tableMatch[1] : 'CoinPoker';

  // Timestamp:  2024/01/15 12:30:45 UTC  or  2024-01-15 12:30:45
  const timeMatch = text.match(/(\d{4}[/-]\d{2}[/-]\d{2}\s+\d{1,2}:\d{2}:\d{2})/);
  const timestamp = timeMatch ? timeMatch[1] : new Date().toISOString();

  // Dealer seat
  const dealerMatch = text.match(/Seat\s*#?(\d+)\s*is the button/i);
  const dealerSeat = dealerMatch ? parseInt(dealerMatch[1]) : 1;

  // Players:  Seat 1: Player1 ($4.00 in chips)  OR  Seat 1: Player1 (4.00 in chips)
  const players: ParsedPlayer[] = [];
  const seatRegex = /Seat (\d+):\s*(.+?)\s*\(\$?([\d,.]+)\s*(?:in chips|USD)?\s*\)/gi;
  let seatMatch;
  while ((seatMatch = seatRegex.exec(text)) !== null) {
    players.push({
      name: seatMatch[2].trim(),
      seat: parseInt(seatMatch[1]),
      chips: parseFloat(seatMatch[3].replace(/,/g, '')),
      isDealer: parseInt(seatMatch[1]) === dealerSeat,
    });
  }
  if (players.length === 0) return null;

  // Hero cards:  Dealt to HeroName [Ah Kd] or [Ah Kd Qs Jc] for Omaha
  let heroName = '';
  let heroCards: string[] = [];
  const dealtMatch = text.match(/Dealt to\s+(.+?)\s*\[([^\]]+)\]/i);
  if (dealtMatch) {
    heroName = dealtMatch[1].trim();
    heroCards = dealtMatch[2].trim().split(/\s+/);
    const heroPlayer = players.find(p => p.name === heroName);
    if (heroPlayer) { heroPlayer.isHero = true; heroPlayer.cards = heroCards; }
  }

  const board = parseBoardCards(text);
  const actions = parseActions(lines, players);
  const { potSize, heroWon, heroNetAmount } = calcHeroResult(actions, heroName);

  return {
    id: handId, site: 'CoinPoker', gameType, stakes, tableName, timestamp,
    players, actions, board, heroCards, heroName, potSize, heroWon, heroNetAmount,
    dealerSeat, raw: text,
  };
}

// ── WPN / ACR / DriveHUD2 parser ────────────────────────────────────

function parseWPNHand(text: string, site: string): ParsedHand | null {
  const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
  if (lines.length < 5) return null;

  const idMatch = text.match(/(?:Game Hand|Hand) #(\d+)/i);
  const handId = idMatch ? idMatch[1] : Date.now().toString();

  const gameType = detectGameType(text);
  const stakes = parseStakes(text);

  const tableMatch = text.match(/Table '([^']+)'/i) || text.match(/Table\s+(\S+)/i);
  const tableName = tableMatch ? tableMatch[1] : 'Unknown';

  const timeMatch = text.match(/(\d{4}[/-]\d{2}[/-]\d{2}\s+\d{1,2}:\d{2}:\d{2})/);
  const timestamp = timeMatch ? timeMatch[1] : new Date().toISOString();

  const dealerMatch = text.match(/Seat #(\d+) is the button/i) || text.match(/Dealer:\s*Seat\s*(\d)/i);
  const dealerSeat = dealerMatch ? parseInt(dealerMatch[1]) : 1;

  const players: ParsedPlayer[] = [];
  const seatRegex = /Seat (\d+): ([^\s(]+)\s*\(?[^)]*?\)?\s*\(\$?([\d,.]+)/gi;
  let seatMatch;
  while ((seatMatch = seatRegex.exec(text)) !== null) {
    players.push({
      name: seatMatch[2].trim(),
      seat: parseInt(seatMatch[1]),
      chips: parseFloat(seatMatch[3].replace(/,/g, '')),
      isDealer: parseInt(seatMatch[1]) === dealerSeat,
    });
  }
  if (players.length === 0) return null;

  let heroName = '';
  let heroCards: string[] = [];
  const dealtMatch = text.match(/Dealt to ([^\[]+)\[([^\]]+)\]/i);
  if (dealtMatch) {
    heroName = dealtMatch[1].trim();
    heroCards = dealtMatch[2].trim().split(/\s+/);
    const heroPlayer = players.find(p => p.name === heroName);
    if (heroPlayer) { heroPlayer.isHero = true; heroPlayer.cards = heroCards; }
  }

  const board = parseBoardCards(text);
  const actions = parseActions(lines, players);
  const { potSize, heroWon, heroNetAmount } = calcHeroResult(actions, heroName);

  return {
    id: handId, site, gameType, stakes, tableName, timestamp,
    players, actions, board, heroCards, heroName, potSize, heroWon, heroNetAmount,
    dealerSeat, raw: text,
  };
}
