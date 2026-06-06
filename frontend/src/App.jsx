import { useEffect, useRef, useState } from "react";
import axios from "axios";
import "./styles/app.css";
import RepoForm from "./features/build/RepoForm";
import PipelineSteps from "./features/build/PipelineSteps";
import ConsoleLog from "./features/build/ConsoleLog";
import ResultPanel from "./features/build/ResultPanel";

const DEFAULT_STAGES = [
  { id: 1, name: "Validate URL", status: "WAITING", message: "" },
  { id: 2, name: "Clone Repository", status: "WAITING", message: "" },
  { id: 3, name: "Analyze Codebase", status: "WAITING", message: "" },
  { id: 4, name: "Generate Dockerfile", status: "WAITING", message: "" },
  { id: 5, name: "Build Docker Image", status: "WAITING", message: "" },
  { id: 6, name: "Run Container", status: "WAITING", message: "" },
  { id: 7, name: "Done", status: "WAITING", message: "" },
];

const STATUS_TONE = {
  SUCCESS: "ok",
  FAILED: "bad",
  RUNNING: "run",
  IDLE: "idle",
};

function App() {
  const [buildId, setBuildId] = useState("");
  const [logs, setLogs] = useState([]);
  const [stages, setStages] = useState(DEFAULT_STAGES);
  const [buildResult, setBuildResult] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [streamStatus, setStreamStatus] = useState("idle");

  const eventSourceRef = useRef(null);

  const resetState = () => {
    setBuildId("");
    setLogs([]);
    setStages(DEFAULT_STAGES);
    setBuildResult(null);
    setIsLoading(false);
    setStreamStatus("idle");
  };

  const closeStream = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  const fetchResult = async (currentBuildId) => {
    try {
      const response = await axios.get(`/api/builds/${currentBuildId}/result`);
      setBuildResult(response.data);

      if (response.data?.stages?.length) {
        setStages(response.data.stages);
      }

      if (response.data?.logs?.length) {
        setLogs(response.data.logs);
      }
    } catch (error) {
      console.error("Failed to fetch final result:", error);
    } finally {
      setIsLoading(false);
      setStreamStatus("closed");
    }
  };

  const openStream = (currentBuildId) => {
    closeStream();

    const eventSource = new EventSource(`/api/builds/${currentBuildId}/stream`);
    eventSourceRef.current = eventSource;
    setStreamStatus("streaming");

    eventSource.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (Array.isArray(payload.logs)) {
          setLogs(payload.logs);
        }

        if (Array.isArray(payload.stages) && payload.stages.length > 0) {
          setStages(payload.stages);
        }

        if (payload.status === "SUCCESS" || payload.status === "FAILED") {
          closeStream();
          await fetchResult(currentBuildId);
        }
      } catch (error) {
        console.error("Failed to parse SSE payload:", error);
      }
    };

    eventSource.onerror = async () => {
      console.warn("SSE connection error or closed.");
      closeStream();
      await fetchResult(currentBuildId);
    };
  };

  const handleSubmit = async (url) => {
    closeStream();
    resetState();
    setIsLoading(true);

    try {
      const response = await axios.post("/api/builds", {
        repo_url: url,
      });

      const newBuildId = response.data.build_id;
      setBuildId(newBuildId);
      openStream(newBuildId);
    } catch (error) {
      console.error("Failed to start build:", error);
      setIsLoading(false);
      setStreamStatus("error");
    }
  };

  useEffect(() => {
    return () => {
      closeStream();
    };
  }, []);

  const buildStatus = buildResult?.status || (isLoading ? "RUNNING" : "IDLE");
  const tone = STATUS_TONE[buildStatus] || "idle";
  const hasStarted = Boolean(buildId) || isLoading;

  return (
    <div className="page">
      <header className="topbar">
        <div className="wrap topbar__inner">
          <div className="brand">
            <span className="brand__mark" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5v-7Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                <path d="M12 12v8M12 12 4 8.5M12 12l8-3.5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
              </svg>
            </span>
            <div className="brand__text">
              <span className="brand__name">DockerDev</span>
              <span className="brand__tag">Containerize any repo</span>
            </div>
          </div>

          <div className={`pill pill--${tone}`}>
            <span className="pill__dot" />
            {buildStatus}
          </div>
        </div>
      </header>

      <main className="wrap stack">
        <section className="hero">
          <div className="hero__copy">
            <span className="hero__eyebrow">
              <span className="pill__dot" />
              Groq-powered · blazing fast
            </span>
            <h1 className="hero__title">
              Turn a GitHub repo into a working <span className="hl">Dockerfile</span>.
            </h1>
            <p className="hero__sub">
              Paste a public repository link. DockerDev clones it, studies the stack,
              writes a Dockerfile, then builds and runs it — live.
            </p>
          </div>
          <RepoForm onSubmit={handleSubmit} isLoading={isLoading} />
        </section>

        {hasStarted && (
          <div className="meta-strip">
            <div className="meta">
              <span className="meta__k">Build</span>
              <span className="meta__v mono">{buildId || "—"}</span>
            </div>
            <div className="meta">
              <span className="meta__k">Connection</span>
              <span className="meta__v mono">{streamStatus}</span>
            </div>
            <div className="meta">
              <span className="meta__k">State</span>
              <span className="meta__v mono">{buildStatus}</span>
            </div>
          </div>
        )}

        {hasStarted && (
          <section className="workspace">
            <div className="panel panel--rail">
              <div className="panel__head">
                <h2 className="panel__title">Pipeline</h2>
              </div>
              <PipelineSteps stages={stages} />
            </div>

            <div className="panel panel--stream">
              <ConsoleLog logs={logs} isStreaming={isLoading} />
            </div>
          </section>
        )}

        <ResultPanel dockerfile={buildResult?.dockerfile || ""} buildId={buildId} />
      </main>

      <footer className="footer wrap">
        <span>DockerDev</span>
        <span>Built for fast, repeatable container setup.</span>
      </footer>
    </div>
  );
}

export default App;
