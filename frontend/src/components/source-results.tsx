export interface SourceResult {
  title: string;
  url: string;
  snippet: string | null;
  source: string | null;
  published_at: string | null;
  query: string | null;
}

interface SourceResultsProps {
  queries: string[];
  sources: SourceResult[];
  totalSources: number;
}

export function SourceResults({ queries, sources, totalSources }: SourceResultsProps) {
  return (
    <section className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
      <h3 className="text-lg font-semibold text-zinc-100">Research Sources</h3>
      <p className="text-sm text-zinc-400">
        {queries.length} search queries · {totalSources} sources found
      </p>
      <ul className="space-y-3">
        {sources.map((source) => (
          <li
            key={source.url}
            className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4 transition hover:border-zinc-700"
          >
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium leading-snug text-zinc-100 transition hover:text-sky-400"
            >
              {source.title}
            </a>
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
              {source.source && (
                <span className="rounded bg-zinc-800 px-1.5 py-0.5">{source.source}</span>
              )}
              {source.published_at && <span>Published: {source.published_at}</span>}
            </div>
            {source.snippet && (
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">{source.snippet}</p>
            )}
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-block text-sm font-medium text-sky-400 transition hover:text-sky-300"
            >
              Open Source →
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
