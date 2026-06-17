/** Format chip/currency amounts for cash vs tournament hands. */
export function formatChips(amount: number, isTournament: boolean): string {
  const abs = Math.abs(amount);
  if (isTournament) {
    return abs.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  return `$${abs.toFixed(2)}`;
}

export function formatChipsSigned(amount: number, isTournament: boolean): string {
  const prefix = amount > 0 ? "+" : amount < 0 ? "-" : "";
  return `${prefix}${formatChips(Math.abs(amount), isTournament)}`;
}
