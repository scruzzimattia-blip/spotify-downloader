(() => {
    "use strict";

    const form = document.getElementById("download-form");
    const urlInput = document.getElementById("url");
    const submitBtn = document.getElementById("submit-btn");
    const refreshBtn = document.getElementById("refresh-btn");
    const jobsContainer = document.getElementById("jobs");
    const emptyState = document.getElementById("empty-state");
    const errorBox = document.getElementById("form-error");
    const jobTemplate = document.getElementById("job-template");

    const STATUS_LABELS = {
        queued: "Warteschlange",
        running: "Läuft",
        completed: "Fertig",
        failed: "Fehler",
    };

    const renderedJobs = new Map();

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.remove("hidden");
    }

    function clearError() {
        errorBox.textContent = "";
        errorBox.classList.add("hidden");
    }

    async function apiFetch(url, options = {}) {
        const res = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try {
                const data = await res.json();
                if (data && data.detail) detail = data.detail;
            } catch (_) {}
            throw new Error(detail);
        }
        if (res.status === 204) return null;
        return res.json();
    }

    function updateJobCard(job) {
        let card = renderedJobs.get(job.id);
        if (!card) {
            card = jobTemplate.content.firstElementChild.cloneNode(true);
            card.dataset.id = job.id;
            card.querySelector(".delete-btn").addEventListener("click", () => {
                deleteJob(job.id);
            });
            jobsContainer.prepend(card);
            renderedJobs.set(job.id, card);
        }

        card.dataset.status = job.status;
        const urlLink = card.querySelector(".job-url");
        urlLink.href = job.url;
        urlLink.textContent = job.url;
        card.querySelector(".status-label").textContent =
            STATUS_LABELS[job.status] || job.status;
        card.querySelector(".job-message").textContent = job.message || "";

        const logPre = card.querySelector(".job-log pre");
        logPre.textContent = (job.log || []).join("\n");

        const filesContainer = card.querySelector(".job-files");
        filesContainer.innerHTML = "";
        if (job.status === "completed" && job.files && job.files.length) {
            for (const filename of job.files) {
                const isArchive = job.archive === filename;
                const link = document.createElement("a");
                link.className = "file-link" + (isArchive ? " primary" : "");
                link.href = `/api/downloads/${job.id}/files/${encodeURIComponent(filename)}`;
                link.download = filename;
                link.innerHTML = `<span class="icon">${isArchive ? "📦" : "🎵"}</span><span>${filename}</span>`;
                filesContainer.appendChild(link);
            }
        }
    }

    function removeJobCard(jobId) {
        const card = renderedJobs.get(jobId);
        if (card) {
            card.remove();
            renderedJobs.delete(jobId);
        }
    }

    function updateEmptyState() {
        if (renderedJobs.size === 0) {
            emptyState.classList.remove("hidden");
            if (!jobsContainer.contains(emptyState)) {
                jobsContainer.appendChild(emptyState);
            }
        } else {
            emptyState.classList.add("hidden");
        }
    }

    async function refreshJobs() {
        try {
            const data = await apiFetch("/api/downloads");
            const seen = new Set();
            for (const job of data.jobs) {
                seen.add(job.id);
                updateJobCard(job);
            }
            for (const id of [...renderedJobs.keys()]) {
                if (!seen.has(id)) removeJobCard(id);
            }
            updateEmptyState();
        } catch (err) {
            console.error("Aktualisierung fehlgeschlagen:", err);
        }
    }

    async function deleteJob(jobId) {
        if (!confirm("Diesen Download wirklich entfernen?")) return;
        try {
            await apiFetch(`/api/downloads/${jobId}`, { method: "DELETE" });
            removeJobCard(jobId);
            updateEmptyState();
        } catch (err) {
            alert(`Löschen fehlgeschlagen: ${err.message}`);
        }
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        clearError();
        const url = urlInput.value.trim();
        if (!url) return;

        submitBtn.disabled = true;
        const labelEl = submitBtn.querySelector(".label");
        const prevLabel = labelEl.textContent;
        labelEl.textContent = "Starte...";

        try {
            const job = await apiFetch("/api/downloads", {
                method: "POST",
                body: JSON.stringify({ url }),
            });
            updateJobCard(job);
            updateEmptyState();
            urlInput.value = "";
        } catch (err) {
            showError(err.message || "Unbekannter Fehler");
        } finally {
            submitBtn.disabled = false;
            labelEl.textContent = prevLabel;
        }
    });

    refreshBtn.addEventListener("click", refreshJobs);

    refreshJobs();
    setInterval(refreshJobs, 2500);
})();
