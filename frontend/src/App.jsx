import { useEffect, useRef, useState } from "react";
import axios from "axios";
import "./App.css";
import InputForm from "./components/InputForm";
import StepTimeline from "./components/StepTimeline";
import LogViewer from "./components/LogViewer";
import DockerfileOutput from "./components/DockerfileOutput";

const DEFAULT_STEPS = [
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
  const [jobId, setJobId] = useState("");
  const [logs, setLogs] = useState([]);
  const [steps, setSteps] = useState(DEFAULT_STEPS);
  const [jobResult, setJobResult] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [streamStatus, setStreamStatus] = useState("idle");

  const eventSourceRef = useRef(null);

  const resetState = () => {
    setJobId("");
    setLogs([]);
    setSteps(DEFAULT_STEPS);
    setJobResult(null);
    setIsLoading(false);
    setStreamStatus("idle");
  };

  const closeStream = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  const fetchResult = async (currentJobId) => {
    try {
      const response = await axios.get(`/api/forge/${currentJobId}/result`);
      setJobResult(response.data);

      if (response.data?.steps?.length) {
        setSteps(response.data.steps);
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

  const openStream = (currentJobId) => {
    closeStream();

    const eventSource = new EventSource(`/api/forge/${currentJobId}/stream`);
    eventSourceRef.current = eventSource;
    setStreamStatus("streaming");

    eventSource.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (Array.isArray(payload.logs)) {
          setLogs(payload.logs);
        }

        if (Array.isArray(payload.steps) && payload.steps.length > 0) {
          setSteps(payload.steps);
        }

        if (payload.status === "SUCCESS" || payload.status === "FAILED") {
          closeStream();
          await fetchResult(currentJobId);
        }
      } catch (error) {
        console.error("Failed to parse SSE payload:", error);
      }
    };

    eventSource.onerror = async () => {
      console.warn("SSE connection error or closed.");
      closeStream();
      await fetchResult(currentJobId);
    };
  };

  const handleSubmit = async (url) => {
    closeStream();
    resetState();
    setIsLoading(true);

    try {
      const response = await axios.post("/api/forge", {
        github_url: url,
      });

      const newJobId = response.data.job_id;
      setJobId(newJobId);
      openStream(newJobId);
    } catch (error) {
      console.error("Failed to start forge job:", error);
      setIsLoading(false);
      setStreamStatus("error");
    }
  };

  useEffect(() => {
    return () => {
      closeStream();
    };
  }, []);

  const jobStatus = jobResult?.status || (isLoading ? "RUNNING" : "IDLE");
  const tone = STATUS_TONE[jobStatus] || "idle";
  const hasStarted = Boolean(jobId) || isLoading;

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
              <span className="brand__name">DockerForge</span>
              <span className="brand__tag">Containerize any repo</span>
            </div>
          </div>

          <div className={`pill pill--${tone}`}>
            <span className="pill__dot" />
            {jobStatus}
          </div>
        </div>
      </header>

      <main className="wrap stack">
        <section className="hero">
          <div className="hero__copy">
            <h1 className="hero__title">
              Turn a GitHub repo into a working <span className="hl">Dockerfile</span>.
            </h1>
            <p className="hero__sub">
              Paste a public repository link. The agent clones it, studies the stack,
              writes a Dockerfile, then builds and runs it — live.
            </p>
          </div>
          <InputForm onSubmit={handleSubmit} isLoading={isLoading} />
        </section>

        {hasStarted && (
          <div className="meta-strip">
            <div className="meta">
              <span className="meta__k">Job</span>
              <span className="meta__v mono">{jobId || "—"}</span>
            </div>
            <div className="meta">
              <span className="meta__k">Connection</span>
              <span className="meta__v mono">{streamStatus}</span>
            </div>
            <div className="meta">
              <span className="meta__k">State</span>
              <span className="meta__v mono">{jobStatus}</span>
            </div>
          </div>
        )}

        {hasStarted && (
          <section className="workspace">
            <div className="panel panel--rail">
              <div className="panel__head">
                <h2 className="panel__title">Pipeline</h2>
              </div>
              <StepTimeline steps={steps} />
            </div>

            <div className="panel panel--stream">
              <LogViewer logs={logs} isStreaming={isLoading} />
            </div>
          </section>
        )}

        <DockerfileOutput dockerfile={jobResult?.dockerfile || ""} jobId={jobId} />
      </main>

      <footer className="footer wrap">
        <span>DockerForge</span>
        <span>Built for fast, repeatable container setup.</span>
      </footer>
    </div>
  );
}

export default App;
