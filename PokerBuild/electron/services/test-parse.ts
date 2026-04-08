import { parseHandHistory } from './handParser';

const text = `Game Hand #2697145531 - Tournament #34635006 - Holdem (No Limit) - Level 3 (800.00/1600.00) - 2026/03/20 01:07:09 UTC
Table '16' 8-max Seat #4 is the button
Seat 1: JohnDaWalka (99450.00)
Seat 2: sandymar (40800.00)
Seat 3: VIANAPDI (188500.00) is sitting out
Seat 4: AdobeHut (95850.00)
Seat 5: MMWITHNUTS (91850.00)
Seat 6: GambitBRA777 (99450.00)
Seat 7: imApony444 (84650.00)
Seat 8: FlpFlopFloyd (99450.00)
JohnDaWalka posts ante 200.00
sandymar posts ante 200.00
VIANAPDI posts ante 200.00
AdobeHut posts ante 200.00
MMWITHNUTS posts ante 200.00
GambitBRA777 posts ante 200.00
imApony444 posts ante 200.00
FlpFlopFloyd posts ante 200.00
MMWITHNUTS posts the small blind 800.00
GambitBRA777 posts the big blind 1600.00
*** HOLE CARDS ***
Main pot 1600.00
Dealt to JohnDaWalka [Ks Ac]
imApony444 folds
FlpFlopFloyd raises 3600.00 to 3600.00
JohnDaWalka calls 3600.00
sandymar folds
VIANAPDI folds
AdobeHut folds
MMWITHNUTS folds
GambitBRA777 folds
*** FLOP *** [Kc 6c As]
Main pot 11200.00
FlpFlopFloyd bets 8400.00
JohnDaWalka calls 8400.00
*** TURN *** [Kc 6c As] [8s]
Main pot 28000.00
FlpFlopFloyd bets 21000.00
JohnDaWalka calls 21000.00
*** RIVER *** [Kc 6c As 8s] [6d]
Main pot 70000.00
FlpFlopFloyd bets 66250.00 and is all-in
JohnDaWalka calls 66250.00 and is all-in
*** SHOW DOWN ***
Main pot 202500.00
JohnDaWalka shows [Ks Ac] (two pair, Aces and Kings [As Ac Ks Kc 8s])
FlpFlopFloyd shows [Jh Ah] (two pair, Aces and Sixs [As Ah 6d 6c Kc])
JohnDaWalka collected 202500.00 from main pot
*** SUMMARY ***
Total pot 202500.00
Board [Kc 6c As 8s 6d]
Seat 1: JohnDaWalka showed [Ks Ac] and won 202500.00 with two pair, Aces and Kings [As Ac Ks Kc 8s]
Seat 2: sandymar folded on the Pre-Flop and did not bet
Seat 3: VIANAPDI folded on the Pre-Flop and did not bet
Seat 4: AdobeHut (button) folded on the Pre-Flop
Seat 5: MMWITHNUTS (small blind) folded on the Pre-Flop
Seat 6: GambitBRA777 (big blind) folded on the Pre-Flop
Seat 7: imApony444 folded on the Pre-Flop and did not bet
Seat 8: FlpFlopFloyd showed [Jh Ah] and lost with two pair, Aces and Sixs [As Ah 6d 6c Kc]`;

const hands = parseHandHistory(text, 'BetACR', 'JohnDaWalka');
console.log(JSON.stringify(hands, null, 2));
