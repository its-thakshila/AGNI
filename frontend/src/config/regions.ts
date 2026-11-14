// Display info per region. Coverage, periods and gaps come from /api/meta.
// Adding California later = one more entry here (and its own backend + model).

export interface Region {
  id: string;
  name: string;
}

export const REGION: Region = { id: 'australia', name: 'Australia' };
