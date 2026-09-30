/**
 * Normalize a place name for matching: strip diacritics (Bihār -> Bihar,
 * Chhattīsgarh -> Chhattisgarh) and lowercase. The database keeps the official
 * GeoBoundaries spellings with macrons; users type plain ASCII, so every
 * search/filter must compare through this function.
 */
export function normName(s: string): string {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
}
