document.addEventListener("DOMContentLoaded", () => {
    const questions = window.examData.questions || [];
    const answers = new Map();
    let current = 0;
    const mount = document.getElementById("questionMount");
    const palette = document.getElementById("questionPalette");
    const saveStatus = document.getElementById("saveStatus");

    function render() {
        const question = questions[current];
        if (!question) {
            mount.innerHTML = "<p class='empty-state'>No questions are available.</p>";
            return;
        }
        mount.innerHTML = `<p class="eyebrow">Question ${current + 1} of ${questions.length}</p>
            <h1>${question.statement}</h1>
            <div class="option-list">${question.options.map((option) => `
            <label class="option-row"><input type="radio" name="answer" value="${option.option_id}" ${answers.get(question.question_id) === option.option_id ? "checked" : ""}><span>${option.option_text}</span></label>`).join("")}</div>`;
        mount.querySelectorAll("input").forEach((input) => input.addEventListener("change", () => {
            answers.set(question.question_id, Number(input.value));
            save(question.question_id, Number(input.value));
            renderPalette();
        }));
        document.getElementById("previousQuestion").disabled = current === 0;
        document.getElementById("nextQuestion").textContent = current === questions.length - 1 ? "Save answer" : "Save & next";
    }

    async function save(questionId, optionId) {
        saveStatus.textContent = "Saving...";
        try {
            const response = await fetch(`/api/attempt/${window.attemptId}/answer`, {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({question_id: questionId, option_id: optionId})
            });
            if (!response.ok) throw new Error("Answer could not be saved.");
            saveStatus.textContent = "Saved";
        } catch (error) {
            saveStatus.textContent = error.message;
        }
    }

    function renderPalette() {
        palette.innerHTML = questions.map((question, index) =>
            `<button type="button" class="palette-button ${answers.has(question.question_id) ? "answered" : ""} ${index === current ? "current" : ""}">${index + 1}</button>`
        ).join("");
        palette.querySelectorAll("button").forEach((button, index) => button.addEventListener("click", () => { current = index; render(); renderPalette(); }));
    }

    document.getElementById("previousQuestion").addEventListener("click", () => { if (current > 0) { current -= 1; render(); renderPalette(); } });
    document.getElementById("nextQuestion").addEventListener("click", () => { if (current < questions.length - 1) { current += 1; render(); renderPalette(); } });
    document.getElementById("submitForm").addEventListener("submit", (event) => {
        if (!window.confirm("Submit this examination? You will not be able to change your answers.")) event.preventDefault();
    });
    render();
    renderPalette();
});
