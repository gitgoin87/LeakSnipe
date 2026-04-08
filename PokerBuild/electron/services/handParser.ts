// DriveHUD2 Hand History Parser
// Parses WPN text, CoinPoker text, PokerStars text, AND DriveHUD2 XML formats
// Supports Hold'em and Omaha formats

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
  dh2HandHistoryId?: number; // DriveHUD2 DB row ID for sync tracking
  isTournament?: boolean;
  tournamentId?: string;
}

// Split concatenated DH2 card string like "AdAsTc8d" into ["Ad","As","Tc","8d"]
export function splitDH2Cards(cardStr: string): string[] {
  if (!cardStr || cardStr.trim().length === 0) return [];
  const cards: string[] = [];
  const s = cardStr.trim();
  for (let i = 0; i < s.length - 1; i += 2) {
    cards.push(s[i] + s[i + 1]);
  }
  return cards;
}

// Simple XML tag value extractor (avoids needing a full XML parser dependency)
function xmlVal(xml: string, tag: string): string {
  const match = xml.match(new RegExp(`<${tag}[^>]*>([^<]*)</${tag}>`, 'i'));
  return match ? match[1].trim() : '';
}

function xmlAttr(element: string, attr: string): string {
  const match = element.match(new RegExp(`${attr}="([^"]*)"`, 'i'));
  return match ? match[1] : '';
}

// Map DH2 XML GameType string to our short code
function mapDH2GameType(gt: string): string {
  const lower = gt.toLowerCase();
  if (lower.includes('nolimitholdem')) return 'NLHE';
  if (lower.includes('potlimitomaha') && lower.includes('hilo')) return 'PLO8';
  if (lower.includes('potlimitomaha')) return 'PLO';
  if (lower.includes('fixedlimitholdem') || lower.includes('limitholdem')) return 'LHE';
  if (lower.includes('potlimitholdem')) return 'PLHE';
  if (lower.includes('5cardomaha') || lower.includes('fivecardpotlimitomaha')) return 'PLO5';
  if (lower.includes('omaha')) return 'PLO';
  if (lower.includes('holdem')) return 'NLHE';
  return 'NLHE';
}

// Map DH2 HandActionType to our action type
function mapDH2ActionType(actionType: string): ParsedAction['type'] | null {
  switch (actionType.toUpperCase()) {
    case 'SMALL_BLIND': case 'BIG_BLIND': case 'ANTE': case 'POSTS': return 'post';
    case 'FOLD': return 'fold';
    case 'CHECK': return 'check';
    case 'CALL': return 'call';
    case 'BET': return 'bet';
    case 'RAISE': return 'raise';
    case 'ALL_IN': return 'allin';
    case 'WINS': case 'WINS_SIDE_POT': return 'win';
    case 'SHOW': case 'MUCKS': return 'show';
    case 'UNCALLED_BET': return null; // tracked separately
    default: return null;
  }
}

// Map DH2 Street string to our street type
function mapDH2Street(street: string): ParsedAction['street'] {
  switch (street.toLowerCase()) {
    case 'preflop': return 'Preflop';
    case 'flop': return 'Flop';
    case 'turn': return 'Turn';
    case 'river': return 'River';
    case 'showdown': case 'summary': return 'Showdown';
    default: return 'Preflop';
  }
}

// Map DH2 numeric PokerSiteId to site name
export function mapDH2SiteId(siteId: number): string {
  const siteMap: Record<number, string> = {
    12: 'BetACR', 13: 'BetACR', // WPN sites
    44: 'CoinPoker',
    1: 'PokerStars',
    2: 'FullTilt',
    20: 'Ignition',
    23: 'Bovada',
    30: 'PartyPoker',
    35: 'GGPoker',
    50: '888Poker',
  };
  return siteMap[siteId] || `Site${siteId}`;
}

/** Parse a single DH2 XML <HandHistory> element into a ParsedHand */
export function parseDH2Xml(xml: string, heroNameOverride?: string): ParsedHand | null {
  if (!xml.includes('<HandHistory>') && !xml.includes('<HandHistory ')) return null;

  try {
    const handId = xmlVal(xml, 'HandId');
    const timestamp = xmlVal(xml, 'DateOfHandUtc');
    const dealerSeat = parseInt(xmlVal(xml, 'DealerButtonPosition')) || 1;
    const tableName = xmlVal(xml, 'TableName');
    const totalPot = parseFloat(xmlVal(xml, 'TotalPot')) || 0;
    const rake = parseFloat(xmlVal(xml, 'Rake')) || 0;
    const heroName = heroNameOverride || xmlVal(xml, 'HeroName') || 'jdwalka';

    // Game description
    const gdMatch = xml.match(/<GameDescription>([\s\S]*?)<\/GameDescription>/i);
    const gd = gdMatch ? gdMatch[1] : '';
    const site = xmlVal(gd, 'Site') || 'CoinPoker';
    const xmlGameType = xmlVal(gd, 'GameType');
    const gameType = mapDH2GameType(xmlGameType);
    const pokerFormat = xmlVal(gd, 'PokerFormat');
    const isTournament = pokerFormat.toLowerCase().includes('tournament');
    const smallBlind = xmlVal(gd, 'SmallBlind');
    const bigBlind = xmlVal(gd, 'BigBlind');
    const stakes = smallBlind && bigBlind ? `${smallBlind}/${bigBlind}` : 'unknown';

    // Parse players
    const players: ParsedPlayer[] = [];
    const playerRegex = /<Player\s+([^>]+)\/>/gi;
    let pm;
    while ((pm = playerRegex.exec(xml)) !== null) {
      const attrs = pm[1];
      const name = xmlAttr(attrs, 'PlayerName');
      const seat = parseInt(xmlAttr(attrs, 'SeatNumber')) || 0;
      const stack = parseFloat(xmlAttr(attrs, 'StartingStack')) || 0;
      const cardsStr = xmlAttr(attrs, 'Cards');
      const cards = splitDH2Cards(cardsStr);
      const isHero = name.toLowerCase() === heroName.toLowerCase();
      players.push({
        name, seat, chips: stack, cards: cards.length > 0 ? cards : undefined,
        isDealer: seat === dealerSeat, isHero,
      });
    }

    if (players.length === 0) return null;

    // Hero cards
    const heroPlayer = players.find(p => p.isHero);
    const heroCards = heroPlayer?.cards || [];

    // Parse actions
    const actions: ParsedAction[] = [];
    const actionRegex = /<HandAction\s+([^>]+)\/>/gi;
    let am;
    while ((am = actionRegex.exec(xml)) !== null) {
      const attrs = am[1];
      const playerName = xmlAttr(attrs, 'PlayerName');
      const actionTypeStr = xmlAttr(attrs, 'HandActionType');
      const amount = parseFloat(xmlAttr(attrs, 'Amount')) || 0;
      const street = xmlAttr(attrs, 'Street');
      const isAllIn = xmlAttr(attrs, 'IsAllIn') === 'true';

      const mappedType = mapDH2ActionType(actionTypeStr);
      if (!mappedType) continue; // skip UNCALLED_BET etc.

      const player = players.find(p => p.name === playerName);
      const finalType = isAllIn && ['call', 'raise', 'bet'].includes(mappedType) ? 'allin' as const : mappedType;

      actions.push({
        playerName,
        playerSeat: player?.seat || 0,
        type: finalType,
        amount: Math.abs(amount), // DH2 uses negative for bets, positive for wins
        street: mapDH2Street(street),
      });
    }

    // Parse community cards
    const ccMatch = xml.match(/<CommunityCards>([^<]+)<\/CommunityCards>/i);
    const board = ccMatch ? splitDH2Cards(ccMatch[1]) : [];

    // Calculate hero net amount
    const heroWinActions = actions.filter(a => a.type === 'win' && a.playerName.toLowerCase() === heroName.toLowerCase());
    const heroWon = heroWinActions.length > 0;
    const heroWinTotal = heroWinActions.reduce((s, a) => s + (a.amount || 0), 0);
    const heroBetTotal = actions
      .filter(a => a.playerName.toLowerCase() === heroName.toLowerCase() && ['post', 'call', 'bet', 'raise', 'allin'].includes(a.type))
      .reduce((s, a) => s + (a.amount || 0), 0);
    const heroNetAmount = heroWon ? (heroWinTotal - heroBetTotal) : -heroBetTotal;

    return {
      id: handId || Date.now().toString(),
      site,
      gameType,
      stakes,
      tableName,
      timestamp: timestamp || new Date().toISOString(),
      players,
      actions,
      board,
      heroCards,
      heroName,
      potSize: totalPot || (actions.filter(a => ['post', 'call', 'bet', 'raise', 'allin'].includes(a.type)).reduce((s, a) => s + (a.amount || 0), 0)),
      heroWon,
      heroNetAmount,
      dealerSeat,
      raw: xml,
      isTournament,
    };
  } catch (_e) {
    return null;
  }
}

export function parseHandHistory(raw: string, defaultSite: string = 'DriveHUD2', heroNameOverride: string = 'jdwalka'): ParsedHand[] {
  const hands: ParsedHand[] = [];

  // Detect DH2 XML format
  if (raw.trimStart().startsWith('<?xml') || raw.includes('<HandHistory>')) {
    // Split multiple XML hand histories if concatenated
    const xmlParts = raw.split(/(?=<\?xml\s)/g).filter(s => s.trim().length > 50);
    for (const xmlPart of xmlParts) {
      try {
        const hand = parseDH2Xml(xmlPart, heroNameOverride);
        if (hand) hands.push(hand);
      } catch (_e) { /* skip unparseable */ }
    }
    if (hands.length > 0) return hands;
  }

  // Fallback: text-based hand history formats (WPN, PokerStars, CoinPoker)
  const handTexts = raw.split(/(?=(?:Winning Poker Network|Game Hand #|Hand #|PokerStars Hand|CoinPoker Hand))/g)
    .filter(t => t.trim().length > 50);

  for (const text of handTexts) {
    try {
      const hand = parseSingleHand(text.trim(), defaultSite, heroNameOverride);
      if (hand) hands.push(hand);
    } catch (_e) {
      // Skip unparseable hands silently
    }
  }
  return hands;
}

function parseSingleHand(text: string, defaultSite: string, heroNameOverride: string = 'jdwalka'): ParsedHand | null {
  const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
  if (lines.length < 5) return null;

  // Detect site from text
  let site = defaultSite;
  if (/CoinPoker Hand/i.test(text)) site = 'CoinPoker';
  else if (/PokerStars Hand/i.test(text)) site = 'PokerStars';
  else if (/Winning Poker Network/i.test(text)) site = 'BetACR';

  // Parse hand ID — supports WPN, PokerStars, and CoinPoker formats
  const idMatch = text.match(/(?:Game Hand|Hand|CoinPoker Hand) #(\d+)/i);
  const handId = idMatch ? idMatch[1] : Date.now().toString();

  // Detect game type
  let gameType = 'NLHE';
  if (/Omaha Hi-Lo/i.test(text)) gameType = 'PLO8';
  else if (/Pot Limit Omaha/i.test(text) || /PLO/i.test(text)) gameType = 'PLO';
  else if (/5 Card Omaha/i.test(text) || /PLO5/i.test(text)) gameType = 'PLO5';
  else if (/No Limit Hold/i.test(text)) gameType = 'NLHE';
  else if (/Pot Limit Hold/i.test(text)) gameType = 'PLHE';
  else if (/Limit Hold/i.test(text)) gameType = 'LHE';

  // Parse stakes
  const stakesMatch = text.match(/\$?([\d.]+)\/\$?([\d.]+)/);
  const stakes = stakesMatch ? `${stakesMatch[1]}/${stakesMatch[2]}` : 'unknown';

  // Parse table name
  const tableMatch = text.match(/Table '([^']+)'/i) || text.match(/Table\s+(\S+)/i);
  const tableName = tableMatch ? tableMatch[1] : 'Unknown';

  // Parse timestamp
  const timeMatch = text.match(/(\d{4}[/-]\d{2}[/-]\d{2}\s+\d{1,2}:\d{2}:\d{2})/);
  const timestamp = timeMatch ? timeMatch[1] : new Date().toISOString();

  // Parse dealer seat
  const dealerMatch = text.match(/Seat #(\d+) is the button/i) || text.match(/Dealer:\s*Seat\s*(\d)/i);
  const dealerSeat = dealerMatch ? parseInt(dealerMatch[1]) : 1;

  // Parse players
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

  // Parse hero cards — "Dealt to PLAYERNAME [Ah Kd]" or "[Ah Kd Qs Jc]" for Omaha
  // Falls back to known hero name if "Dealt to" line is absent
  const KNOWN_HERO_NAME = heroNameOverride || 'jdwalka';
  let heroName = '';
  let heroCards: string[] = [];
  const dealtMatch = text.match(/Dealt to ([^\[]+)\[([^\]]+)\]/i);
  if (dealtMatch) {
    heroName = dealtMatch[1].trim();
    heroCards = dealtMatch[2].trim().split(/\s+/);
    const heroPlayer = players.find(p => p.name === heroName);
    if (heroPlayer) {
      heroPlayer.isHero = true;
      heroPlayer.cards = heroCards;
    }
  }

  // Fallback: if hero still unknown, try to match by known name
  if (!heroName) {
    const knownHero = players.find(p => p.name.toLowerCase() === KNOWN_HERO_NAME.toLowerCase());
    if (knownHero) {
      heroName = knownHero.name;
      knownHero.isHero = true;
    }
  }

  // Parse board
  let board: string[] = [];
  const boardMatch = text.match(/Board:\s*\[([^\]]+)\]/i) || text.match(/\*{3}\s*FLOP\s*\*{3}\s*\[([^\]]+)\]/i);
  if (boardMatch) {
    board = boardMatch[1].trim().split(/\s+/);
  }
  // Also collect from FLOP/TURN/RIVER lines
  const flopMatch = text.match(/\*{3}\s*FLOP\s*\*{3}\s*\[([^\]]+)\]/i);
  const turnMatch = text.match(/\*{3}\s*TURN\s*\*{3}\s*\[[^\]]+\]\s*\[([^\]]+)\]/i);
  const riverMatch = text.match(/\*{3}\s*RIVER\s*\*{3}\s*\[[^\]]+\]\s*\[([^\]]+)\]/i);
  if (flopMatch) board = flopMatch[1].trim().split(/\s+/);
  if (turnMatch) board.push(...turnMatch[1].trim().split(/\s+/));
  if (riverMatch) board.push(...riverMatch[1].trim().split(/\s+/));

  // Parse actions by street
  const actions: ParsedAction[] = [];
  let currentStreet: ParsedAction['street'] = 'Preflop';

  for (const line of lines) {
    // Street markers
    if (/\*{3}\s*FLOP/i.test(line)) { currentStreet = 'Flop'; continue; }
    if (/\*{3}\s*TURN/i.test(line)) { currentStreet = 'Turn'; continue; }
    if (/\*{3}\s*RIVER/i.test(line)) { currentStreet = 'River'; continue; }
    if (/\*{3}\s*SHOW\s*DOWN/i.test(line)) { currentStreet = 'Showdown'; continue; }

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

    // All-in (often appears as "raises ... and is all-in" or "calls ... and is all-in")
    if (/all-in/i.test(line) && actions.length > 0) {
      const lastAction = actions[actions.length - 1];
      if (lastAction) lastAction.type = 'allin';
    }

    // Wins pot
    const winMatch = line.match(/^(.+?)\s+(?:collected|wins)\s+\$?([\d,.]+)/i);
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

  // Calculate pot and hero result
  const potSize = actions
    .filter(a => ['post', 'call', 'bet', 'raise', 'allin'].includes(a.type))
    .reduce((sum, a) => sum + (a.amount || 0), 0);

  const heroWinAction = actions.find(a => a.type === 'win' && a.playerName === heroName);
  const heroWon = !!heroWinAction;
  const heroBets = actions
    .filter(a => a.playerName === heroName && ['post', 'call', 'bet', 'raise', 'allin'].includes(a.type))
    .reduce((sum, a) => sum + (a.amount || 0), 0);
  const heroNetAmount = heroWon ? (heroWinAction!.amount! - heroBets) : -heroBets;

  return {
    id: handId,
    site,
    gameType,
    stakes,
    tableName,
    timestamp,
    players,
    actions,
    board,
    heroCards,
    heroName,
    potSize,
    heroWon,
    heroNetAmount,
    dealerSeat,
    raw: text
  };
}
