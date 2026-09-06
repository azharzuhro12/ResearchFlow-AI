import { BackendStatus } from "@/components/backend-status";
import { RecentReports } from "@/components/recent-reports";
import { ResearchForm } from "@/components/research-form";
import { SchedulerPanel } from "@/components/scheduler-panel";

export default function Home() {
  return (
    <main className="relative min-h-screen bg-zinc-950 text-zinc-100">
      <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-6 pb-16 pt-16 sm:pt-20">
        <header className="flex flex-col items-center gap-5 text-center">
          <div className="space-y-3">
            <p className="text-xs font-medium uppercase tracking-[0.3em] text-sky-400">
              Autonomous · Scheduled · Grounded
            </p>
            <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
              ResearchFlow AI
            </h1>
            <p className="text-lg text-zinc-400">
              Autonomous AI Research Automation Platform
            </p>
          </div>
          <BackendStatus />
        </header>

        <section className="mt-14 flex-1">
          <ResearchForm />
          <SchedulerPanel />
          <RecentReports />
        </section>

        <footer className="pt-12 text-center text-xs text-zinc-600">
          Next.js → FastAPI → GLM API → ChromaDB → Citations → Reports →
          Scheduler → Discord → SQLite
        </footer>
      </div>
    </main>
  );
}
