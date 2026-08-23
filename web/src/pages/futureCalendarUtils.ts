export function buildMonthDays(start: Date): Array<Date | null> {
  const first = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  const mondayOffset = (first.getUTCDay() + 6) % 7;
  const result: Array<Date | null> = [];
  for (let index = -mondayOffset; index < 42 - mondayOffset; index += 1) {
    result.push(new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1 + index)));
  }
  return result;
}
