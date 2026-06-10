/**
 * Document languages for widget `12_documents_languages`.
 * Must match `lang_choices` in dbx_inspire_ai_agent.ipynb (multiselect setup cell).
 */
export const DOCUMENT_LANGUAGE_CHOICES = [
  'English',
  'French',
  'German',
  'Spanish',
  'Hindi',
  'Chinese (Mandarin)',
  'Japanese',
  'Arabic',
  'Portuguese',
  'Russian',
  'Swedish',
  'Danish',
  'Norwegian',
  'Finnish',
  'Italian',
  'Polish',
  'Romanian',
  'Ukrainian',
  'Dutch',
  'Korean',
  'Indonesian',
  'Malay',
  'Tamil',
];

/** Grouped for UI search/browse (same 23 languages as notebook). */
export const DOCUMENT_LANGUAGES_GROUPED = [
  { group: 'Common', items: DOCUMENT_LANGUAGE_CHOICES.slice(0, 10) },
  { group: 'Nordic & European', items: DOCUMENT_LANGUAGE_CHOICES.slice(10, 19) },
  { group: 'Asian & other', items: DOCUMENT_LANGUAGE_CHOICES.slice(19) },
];

const CHOICE_SET = new Set(DOCUMENT_LANGUAGE_CHOICES);

/** Parse notebook widget value (comma-separated) into validated language names. */
export function parseDocumentLanguages(value) {
  const raw = Array.isArray(value) ? value : String(value ?? '').split(',');
  const out = [];
  for (const part of raw) {
    const name = String(part).trim();
    if (name && CHOICE_SET.has(name) && !out.includes(name)) {
      out.push(name);
    }
  }
  return out;
}

/** Format for notebook widget `12_documents_languages`. */
export function formatDocumentLanguages(languages) {
  return parseDocumentLanguages(languages).join(',');
}
