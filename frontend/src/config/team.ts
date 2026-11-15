// Shown in the About dialog.

export interface Member {
  name: string;
  github: string;
}

export const TEAM = {
  name: 'Team AGNI',
  event: 'NASA Space Apps Challenge 2026',
  members: [
    { name: 'Thakshila Bandara', github: 'https://github.com/its-thakshila' },
    { name: 'Induwara Amaranga', github: 'https://github.com/induwara-amaranga' },
    { name: 'Lahiru Dinushan', github: 'https://github.com/Lahiru-dinu' },
    { name: 'Shamiska Darshana', github: 'https://github.com/ShamiD-1904' },
  ] as Member[],
  repository: 'https://github.com/its-thakshila/AGNI',
};
