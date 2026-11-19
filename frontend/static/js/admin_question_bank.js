/**
 * EXAMSHIELD AI - Question Bank Module
 * Comprehensive client-side functionality for managing reusable questions,
 * advanced filtering, previewing, dynamic type editing, bulk selection,
 * duplication, and importing into examinations.
 */

(function () {
    'use strict';

    // State
    let selectedQuestionIds = new Set();
    let currentEditingQuestionId = null;
    let editorOptions = [];

    // DOM Elements Cache
    let elements = {};

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheDOMElements();
        bindEvents();
        updateBulkBar();
    }

    function cacheDOMElements() {
        elements = {
            csrfToken: document.querySelector('input[name="csrf_token"]')?.value || '',
            selectAllCheckbox: document.getElementById('select-all-questions'),
            rowCheckboxes: document.querySelectorAll('.q-row-checkbox'),
            bulkBar: document.getElementById('bank-bulk-bar'),
            bulkCountText: document.getElementById('bulk-selected-count'),
            btnBulkDeselect: document.getElementById('btn-bulk-deselect'),
            btnBulkImport: document.getElementById('btn-bulk-import'),
            btnBulkDelete: document.getElementById('btn-bulk-delete'),

            // Modals
            modalEditor: document.getElementById('modal-question-editor'),
            editorForm: document.getElementById('question-editor-form'),
            editorTitle: document.getElementById('editor-modal-title'),
            editorStatement: document.getElementById('editor-statement'),
            editorSubject: document.getElementById('editor-subject'),
            editorSubjectCustom: document.getElementById('editor-subject-custom'),
            editorTopic: document.getElementById('editor-topic'),
            editorDifficulty: document.getElementById('editor-difficulty'),
            editorType: document.getElementById('editor-type'),
            editorMarks: document.getElementById('editor-marks'),
            editorNegativeMarks: document.getElementById('editor-negative-marks'),
            editorExplanation: document.getElementById('editor-explanation'),
            editorImageUrl: document.getElementById('editor-image-url'),
            editorImgPreview: document.getElementById('editor-img-preview'),
            editorAnswersContainer: document.getElementById('editor-answers-container'),
            btnSaveQuestion: document.getElementById('btn-save-question'),

            // Preview Modal
            modalPreview: document.getElementById('modal-question-preview'),
            previewContent: document.getElementById('preview-modal-body'),

            // Import Modal
            modalImport: document.getElementById('modal-import-exam'),
            importCountText: document.getElementById('import-selected-count'),
            importTargetExam: document.getElementById('import-target-exam'),
            btnConfirmImport: document.getElementById('btn-confirm-import'),

            // Alerts
            alertBanner: document.getElementById('bank-alert'),
        };
    }

    function bindEvents() {
        // Master Select All
        if (elements.selectAllCheckbox) {
            elements.selectAllCheckbox.addEventListener('change', function () {
                const checked = this.checked;
                elements.rowCheckboxes.forEach(cb => {
                    cb.checked = checked;
                    const qId = parseInt(cb.value, 10);
                    if (checked) selectedQuestionIds.add(qId);
                    else selectedQuestionIds.delete(qId);

                    const tr = cb.closest('tr');
                    if (tr) tr.classList.toggle('selected-row', checked);
                });
                updateBulkBar();
            });
        }

        // Individual Checkboxes
        elements.rowCheckboxes.forEach(cb => {
            cb.addEventListener('change', function () {
                const qId = parseInt(this.value, 10);
                if (this.checked) selectedQuestionIds.add(qId);
                else selectedQuestionIds.delete(qId);

                const tr = this.closest('tr');
                if (tr) tr.classList.toggle('selected-row', this.checked);

                if (elements.selectAllCheckbox) {
                    elements.selectAllCheckbox.checked = (selectedQuestionIds.size === elements.rowCheckboxes.length);
                }
                updateBulkBar();
            });
        });

        // Bulk Deselect
        if (elements.btnBulkDeselect) {
            elements.btnBulkDeselect.addEventListener('click', () => {
                selectedQuestionIds.clear();
                elements.rowCheckboxes.forEach(cb => {
                    cb.checked = false;
                    const tr = cb.closest('tr');
                    if (tr) tr.classList.remove('selected-row');
                });
                if (elements.selectAllCheckbox) elements.selectAllCheckbox.checked = false;
                updateBulkBar();
            });
        }

        // Bulk Delete
        if (elements.btnBulkDelete) {
            elements.btnBulkDelete.addEventListener('click', handleBulkDelete);
        }

        // Bulk Import
        if (elements.btnBulkImport) {
            elements.btnBulkImport.addEventListener('click', () => {
                if (selectedQuestionIds.size === 0) return;
                openImportModal(Array.from(selectedQuestionIds));
            });
        }

        // Add Question button
        document.querySelectorAll('[data-action="open-add-modal"]').forEach(btn => {
            btn.addEventListener('click', openAddModal);
        });

        // Row Action Buttons
        document.querySelectorAll('[data-action="preview-question"]').forEach(btn => {
            btn.addEventListener('click', function () {
                const qId = parseInt(this.dataset.id, 10);
                openPreviewModal(qId);
            });
        });

        document.querySelectorAll('[data-action="edit-question"]').forEach(btn => {
            btn.addEventListener('click', function () {
                const qId = parseInt(this.dataset.id, 10);
                openEditModal(qId);
            });
        });

        document.querySelectorAll('[data-action="duplicate-question"]').forEach(btn => {
            btn.addEventListener('click', function () {
                const qId = parseInt(this.dataset.id, 10);
                handleDuplicateQuestion(qId);
            });
        });

        document.querySelectorAll('[data-action="delete-question"]').forEach(btn => {
            btn.addEventListener('click', function () {
                const qId = parseInt(this.dataset.id, 10);
                handleDeleteQuestion(qId);
            });
        });

        document.querySelectorAll('[data-action="import-single-question"]').forEach(btn => {
            btn.addEventListener('click', function () {
                const qId = parseInt(this.dataset.id, 10);
                openImportModal([qId]);
            });
        });

        // Close Modals
        document.querySelectorAll('[data-action="close-modal"]').forEach(btn => {
            btn.addEventListener('click', function () {
                const modal = this.closest('.modal-overlay');
                if (modal) modal.style.display = 'none';
            });
        });

        // Custom Subject Toggle in Editor
        if (elements.editorSubject) {
            elements.editorSubject.addEventListener('change', function () {
                if (this.value === '__custom__') {
                    elements.editorSubjectCustom.style.display = 'block';
                    elements.editorSubjectCustom.focus();
                } else {
                    elements.editorSubjectCustom.style.display = 'none';
                }
            });
        }

        // Editor Question Type Change
        if (elements.editorType) {
            elements.editorType.addEventListener('change', function () {
                renderEditorAnswerInputs(this.value);
            });
        }

        // Image URL Preview in Editor
        if (elements.editorImageUrl && elements.editorImgPreview) {
            elements.editorImageUrl.addEventListener('input', function () {
                const val = this.value.trim();
                const img = elements.editorImgPreview.querySelector('img');
                if (val && img) {
                    img.src = val;
                    elements.editorImgPreview.style.display = 'block';
                } else {
                    elements.editorImgPreview.style.display = 'none';
                }
            });
        }

        // Save Question from Editor Modal
        if (elements.btnSaveQuestion) {
            elements.btnSaveQuestion.addEventListener('click', handleSaveQuestion);
        }

        // Confirm Import
        if (elements.btnConfirmImport) {
            elements.btnConfirmImport.addEventListener('click', handleConfirmImport);
        }

        // Filter Form Auto-submit on change
        const filterForm = document.getElementById('bank-filters-form');
        if (filterForm) {
            filterForm.querySelectorAll('select').forEach(sel => {
                sel.addEventListener('change', () => filterForm.submit());
            });
        }
    }

    function updateBulkBar() {
        const count = selectedQuestionIds.size;
        if (elements.bulkBar) {
            elements.bulkBar.style.display = count > 0 ? 'flex' : 'none';
        }
        if (elements.bulkCountText) {
            elements.bulkCountText.textContent = `${count} selected`;
        }
    }

    // ==========================================
    // Question Preview Modal
    // ==========================================
    async function openPreviewModal(questionId) {
        if (!elements.modalPreview || !elements.previewContent) return;
        elements.previewContent.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-muted);">Loading question details...</div>';
        elements.modalPreview.style.display = 'flex';

        try {
            const res = await fetch(`/admin/api/question-bank/${questionId}`);
            if (!res.ok) throw new Error('Failed to load question details.');
            const data = await res.json();
            const q = data.question;

            let optionsHtml = '';
            if (q.options && q.options.length > 0) {
                optionsHtml = `
                    <div style="margin-top:1rem;">
                        <strong style="display:block; margin-bottom:6px; font-size:0.85rem; color:var(--text-secondary); text-transform:uppercase;">Answer Options:</strong>
                        <ol class="preview-options-list" type="A">
                            ${q.options.map(opt => `
                                <li class="${opt.is_correct ? 'correct-highlight' : ''}" style="margin-bottom:6px;">
                                    ${escapeHtml(opt.option_text)}
                                    ${opt.is_correct ? '<span style="font-size:0.75rem; background:#dcfce7; color:#166534; padding:2px 6px; border-radius:4px; margin-left:6px; font-weight:bold;">(Correct Answer)</span>' : ''}
                                </li>
                            `).join('')}
                        </ol>
                    </div>
                `;
            } else if (q.correct_answer_text) {
                optionsHtml = `
                    <div style="margin-top:1rem; padding:10px 12px; background:#f0fdf4; border-left:3px solid #22c55e; border-radius:4px;">
                        <strong style="color:#166534; font-size:0.85rem;">Correct Answer / Model Response:</strong>
                        <div style="font-size:0.92rem; color:#14532d; margin-top:2px; font-weight:600;">${escapeHtml(q.correct_answer_text)}</div>
                    </div>
                `;
            }

            elements.previewContent.innerHTML = `
                <div style="margin-bottom:1.25rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                    <div>
                        <span class="badge badge-neutral">${escapeHtml(q.subject_name || 'General')}</span>
                        <span class="badge badge-neutral">${getTypeLabel(q.question_type)}</span>
                        <span class="badge-${getDifficultyClass(q.difficulty)}">${q.difficulty}</span>
                        ${q.topic ? `<span class="chip" style="font-size:0.75rem;">${escapeHtml(q.topic)}</span>` : ''}
                    </div>
                    <div>
                        <strong style="color:var(--primary); font-size:1rem;">${q.marks} Marks</strong>
                        ${q.negative_marks ? `<small style="color:var(--danger); margin-left:6px;">(-${q.negative_marks})</small>` : ''}
                    </div>
                </div>

                <div style="font-size:1.05rem; font-weight:600; color:var(--text-primary); line-height:1.5; margin-bottom:1rem; white-space:pre-line;">
                    ${escapeHtml(q.statement)}
                </div>

                ${q.image_url ? `
                    <div style="margin:1rem 0;">
                        <img src="${escapeHtml(q.image_url)}" alt="Question Figure" style="max-height:220px; border-radius:6px; border:1px solid var(--border-color);">
                    </div>
                ` : ''}

                ${optionsHtml}

                ${q.explanation ? `
                    <div style="margin-top:1.25rem; padding:10px 12px; background:var(--bg-card-subtle); border-radius:var(--radius-md); border:1px solid var(--border-color);">
                        <strong style="font-size:0.82rem; color:var(--text-secondary); text-transform:uppercase;">Explanation / Rubric:</strong>
                        <div style="font-size:0.88rem; color:var(--text-primary); margin-top:4px; white-space:pre-line;">${escapeHtml(q.explanation)}</div>
                    </div>
                ` : ''}

                <div style="margin-top:1.5rem; font-size:0.78rem; color:var(--text-muted); border-top:1px solid var(--border-color); padding-top:8px; display:flex; justify-content:space-between;">
                    <span>Created by: <strong>${escapeHtml(q.creator_name || 'Faculty Member')}</strong></span>
                    <span>Date: ${q.created_at || 'Pre-configured'}</span>
                </div>
            `;
        } catch (err) {
            elements.previewContent.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--danger);">Error loading question details. Please try again.</div>';
        }
    }

    // ==========================================
    // Add & Edit Question Modal
    // ==========================================
    function openAddModal() {
        currentEditingQuestionId = null;
        if (!elements.modalEditor) return;
        elements.editorTitle.textContent = 'Add New Reusable Question';
        elements.editorStatement.value = '';
        if (elements.editorSubject) elements.editorSubject.selectedIndex = 0;
        if (elements.editorSubjectCustom) {
            elements.editorSubjectCustom.value = '';
            elements.editorSubjectCustom.style.display = 'none';
        }
        elements.editorTopic.value = '';
        elements.editorDifficulty.value = 'Medium';
        elements.editorType.value = 'mcq';
        elements.editorMarks.value = '1';
        elements.editorNegativeMarks.value = '0';
        elements.editorExplanation.value = '';
        elements.editorImageUrl.value = '';
        if (elements.editorImgPreview) elements.editorImgPreview.style.display = 'none';

        editorOptions = [
            { option_text: 'Option 1', is_correct: true },
            { option_text: 'Option 2', is_correct: false },
            { option_text: 'Option 3', is_correct: false },
            { option_text: 'Option 4', is_correct: false },
        ];
        renderEditorAnswerInputs('mcq');
        elements.modalEditor.style.display = 'flex';
        elements.editorStatement.focus();
    }

    async function openEditModal(questionId) {
        currentEditingQuestionId = questionId;
        if (!elements.modalEditor) return;
        elements.editorTitle.textContent = 'Edit Reusable Question';

        try {
            const res = await fetch(`/admin/api/question-bank/${questionId}`);
            if (!res.ok) throw new Error('Question not found or permission denied.');
            const data = await res.json();
            const q = data.question;

            elements.editorStatement.value = q.statement || '';
            if (elements.editorSubject) {
                elements.editorSubject.value = q.subject_id;
                if (!elements.editorSubject.value) {
                    elements.editorSubject.value = '__custom__';
                    elements.editorSubjectCustom.value = q.subject_name || '';
                    elements.editorSubjectCustom.style.display = 'block';
                } else {
                    elements.editorSubjectCustom.style.display = 'none';
                }
            }
            elements.editorTopic.value = q.topic || '';
            elements.editorDifficulty.value = q.difficulty || 'Medium';
            elements.editorType.value = q.question_type || 'mcq';
            elements.editorMarks.value = q.marks || 1;
            elements.editorNegativeMarks.value = q.negative_marks || 0;
            elements.editorExplanation.value = q.explanation || '';
            elements.editorImageUrl.value = q.image_url || '';

            if (elements.editorImgPreview) {
                if (q.image_url) {
                    elements.editorImgPreview.querySelector('img').src = q.image_url;
                    elements.editorImgPreview.style.display = 'block';
                } else {
                    elements.editorImgPreview.style.display = 'none';
                }
            }

            editorOptions = (q.options || []).map(o => ({
                option_text: o.option_text,
                is_correct: Boolean(o.is_correct),
            }));

            renderEditorAnswerInputs(q.question_type || 'mcq', q.correct_answer_text);
            elements.modalEditor.style.display = 'flex';
            elements.editorStatement.focus();
        } catch (err) {
            showAlert('Could not load question for editing: ' + err.message, 'danger');
        }
    }

    function renderEditorAnswerInputs(type, defaultText = '') {
        const container = elements.editorAnswersContainer;
        if (!container) return;
        container.innerHTML = '';

        if (type === 'mcq' || type === 'multiple_select') {
            const isRadio = (type === 'mcq');
            if (editorOptions.length < 2) {
                editorOptions = [
                    { option_text: 'Option 1', is_correct: true },
                    { option_text: 'Option 2', is_correct: false },
                    { option_text: 'Option 3', is_correct: false },
                    { option_text: 'Option 4', is_correct: false },
                ];
            }

            const header = document.createElement('div');
            header.style.display = 'flex';
            header.style.justifyContent = 'space-between';
            header.style.alignItems = 'center';
            header.style.marginBottom = '6px';
            header.innerHTML = `
                <label style="font-weight:600; margin:0;">Answer Options <span style="font-size:0.75rem; color:var(--text-muted); font-weight:normal;">(Select the ${isRadio ? 'single correct option' : 'correct checkbox option(s)'})</span></label>
            `;
            container.appendChild(header);

            const shell = document.createElement('div');
            shell.className = 'options-editor-shell';

            editorOptions.forEach((opt, idx) => {
                const row = document.createElement('div');
                row.className = `builder-option-row ${opt.is_correct ? 'is-correct' : ''}`;
                row.innerHTML = `
                    <label class="option-correct-picker" title="Mark as correct">
                        <input type="${isRadio ? 'radio' : 'checkbox'}" name="editor_opt_corr" ${opt.is_correct ? 'checked' : ''}>
                    </label>
                    <span style="font-weight:700; color:var(--text-muted); font-size:0.85rem; width:18px;">${String.fromCharCode(65 + idx)}.</span>
                    <input type="text" class="option-text-input" value="${escapeHtml(opt.option_text)}" placeholder="Enter option text...">
                    <button type="button" class="btn-option-remove" title="Remove option">&times;</button>
                `;

                row.querySelector('input[type="radio"], input[type="checkbox"]').addEventListener('change', function () {
                    if (isRadio) {
                        editorOptions.forEach((o, i) => o.is_correct = (i === idx));
                    } else {
                        opt.is_correct = this.checked;
                    }
                    renderEditorAnswerInputs(type);
                });

                row.querySelector('.option-text-input').addEventListener('input', function () {
                    opt.option_text = this.value;
                });

                row.querySelector('.btn-option-remove').addEventListener('click', function () {
                    if (editorOptions.length <= 2) {
                        alert('Multiple-choice questions require at least two options.');
                        return;
                    }
                    editorOptions.splice(idx, 1);
                    renderEditorAnswerInputs(type);
                });

                shell.appendChild(row);
            });

            const addBtn = document.createElement('button');
            addBtn.type = 'button';
            addBtn.className = 'btn-add-option';
            addBtn.textContent = '+ Add Option';
            addBtn.addEventListener('click', () => {
                editorOptions.push({ option_text: `Option ${editorOptions.length + 1}`, is_correct: false });
                renderEditorAnswerInputs(type);
            });

            container.appendChild(shell);
            container.appendChild(addBtn);

        } else if (type === 'true_false') {
            const trueCorrect = editorOptions.find(o => o.option_text === 'True')?.is_correct ?? true;
            container.innerHTML = `
                <label style="font-weight:600; display:block; margin-bottom:6px;">Correct Answer</label>
                <div style="display:flex; gap:20px;">
                    <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:600;">
                        <input type="radio" name="tf_answer" value="True" ${trueCorrect ? 'checked' : ''}> True
                    </label>
                    <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:600;">
                        <input type="radio" name="tf_answer" value="False" ${!trueCorrect ? 'checked' : ''}> False
                    </label>
                </div>
            `;

        } else if (type === 'fill_blank') {
            container.innerHTML = `
                <div class="form-group">
                    <label style="font-weight:600;">Accepted Blank Answer(s) <span style="font-size:0.75rem; color:var(--text-muted); font-weight:normal;">(Separate acceptable variations with comma)</span></label>
                    <input type="text" id="editor-correct-text" class="form-control" value="${escapeHtml(defaultText)}" placeholder="e.g. CPU, Central Processing Unit">
                </div>
            `;

        } else if (type === 'short_answer') {
            container.innerHTML = `
                <div class="form-group">
                    <label style="font-weight:600;">Model Answer / Essential Key Phrases</label>
                    <input type="text" id="editor-correct-text" class="form-control" value="${escapeHtml(defaultText)}" placeholder="e.g. Encapsulation bundles data and methods together...">
                </div>
            `;

        } else if (type === 'long_answer') {
            container.innerHTML = `
                <div class="form-group">
                    <label style="font-weight:600;">Evaluation Rubric & Essential Concepts</label>
                    <textarea id="editor-correct-text" class="form-control" rows="3" placeholder="Specify required technical details, diagram criteria, and mark distribution...">${escapeHtml(defaultText)}</textarea>
                </div>
            `;

        } else if (type === 'numerical') {
            container.innerHTML = `
                <div class="form-group">
                    <label style="font-weight:600;">Correct Numerical Value</label>
                    <input type="number" step="any" id="editor-correct-text" class="form-control" value="${escapeHtml(defaultText)}" placeholder="e.g. 42 or 3.14">
                </div>
            `;
        }
    }

    async function handleSaveQuestion() {
        const stmt = elements.editorStatement?.value.trim();
        if (!stmt) {
            alert('Please provide the question statement.');
            elements.editorStatement?.focus();
            return;
        }

        let subjectId = elements.editorSubject?.value;
        let subjectName = null;
        if (subjectId === '__custom__') {
            subjectName = elements.editorSubjectCustom?.value.trim();
            subjectId = null;
            if (!subjectName) {
                alert('Please enter the custom subject name.');
                elements.editorSubjectCustom?.focus();
                return;
            }
        }

        const topic = elements.editorTopic?.value.trim() || 'General';
        const type = elements.editorType?.value || 'mcq';
        const difficulty = elements.editorDifficulty?.value || 'Medium';
        const marks = parseFloat(elements.editorMarks?.value || 1);
        const negMarks = parseFloat(elements.editorNegativeMarks?.value || 0);
        const explanation = elements.editorExplanation?.value.trim() || '';
        const imageUrl = elements.editorImageUrl?.value.trim() || '';

        let correctText = '';
        let optionsToSend = [];

        if (type === 'mcq' || type === 'multiple_select') {
            if (editorOptions.length < 2) {
                alert('At least two options are required.');
                return;
            }
            const hasCorr = editorOptions.some(o => o.is_correct);
            if (!hasCorr) {
                alert('Please select at least one correct option.');
                return;
            }
            optionsToSend = editorOptions;
        } else if (type === 'true_false') {
            const tfVal = document.querySelector('input[name="tf_answer"]:checked')?.value || 'True';
            optionsToSend = [
                { option_text: 'True', is_correct: tfVal === 'True' },
                { option_text: 'False', is_correct: tfVal === 'False' },
            ];
        } else {
            correctText = document.getElementById('editor-correct-text')?.value.trim() || '';
            if (!correctText && type !== 'long_answer') {
                alert('Please specify the correct answer or grading guideline.');
                return;
            }
        }

        const payload = {
            csrf_token: elements.csrfToken,
            statement: stmt,
            subject_id: subjectId ? parseInt(subjectId, 10) : null,
            subject_name: subjectName,
            topic: topic,
            question_type: type,
            difficulty: difficulty,
            marks: marks,
            negative_marks: negMarks,
            explanation: explanation,
            correct_answer_text: correctText,
            image_url: imageUrl,
            options: optionsToSend,
        };

        const isEdit = (currentEditingQuestionId !== null);
        const url = isEdit
            ? `/admin/question-bank/${currentEditingQuestionId}/edit`
            : `/admin/question-bank/create`;

        try {
            elements.btnSaveQuestion.disabled = true;
            elements.btnSaveQuestion.textContent = 'Saving...';

            const res = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': elements.csrfToken,
                },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showAlert(data.message || 'Question saved successfully!', 'success');
                setTimeout(() => window.location.reload(), 600);
            } else {
                alert(data.error || 'Failed to save question.');
            }
        } catch (err) {
            alert('Server or network error while saving question.');
        } finally {
            elements.btnSaveQuestion.disabled = false;
            elements.btnSaveQuestion.textContent = 'Save Question';
        }
    }

    // ==========================================
    // Duplicate & Delete Handlers
    // ==========================================
    async function handleDuplicateQuestion(questionId) {
        if (!confirm('Do you want to create a duplicate copy of this question?')) return;
        try {
            const res = await fetch(`/admin/question-bank/${questionId}/duplicate`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': elements.csrfToken,
                },
                body: JSON.stringify({ csrf_token: elements.csrfToken }),
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showAlert(data.message || 'Question duplicated successfully!', 'success');
                setTimeout(() => window.location.reload(), 600);
            } else {
                alert(data.error || 'Could not duplicate question.');
            }
        } catch (err) {
            alert('Error connecting to server.');
        }
    }

    async function handleDeleteQuestion(questionId) {
        if (!confirm('Are you sure you want to permanently delete this question from the Question Bank?')) return;
        try {
            const res = await fetch(`/admin/question-bank/${questionId}/delete`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': elements.csrfToken,
                },
                body: JSON.stringify({ csrf_token: elements.csrfToken }),
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showAlert(data.message || 'Question deleted.', 'success');
                setTimeout(() => window.location.reload(), 600);
            } else {
                alert(data.error || 'Could not delete question. You may lack permission.');
            }
        } catch (err) {
            alert('Error connecting to server.');
        }
    }

    async function handleBulkDelete() {
        if (selectedQuestionIds.size === 0) return;
        const count = selectedQuestionIds.size;
        if (!confirm(`Are you sure you want to delete ${count} selected question(s)?`)) return;

        try {
            const res = await fetch('/admin/question-bank/bulk-delete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': elements.csrfToken,
                },
                body: JSON.stringify({
                    csrf_token: elements.csrfToken,
                    question_ids: Array.from(selectedQuestionIds),
                }),
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showAlert(data.message || 'Questions deleted.', 'success');
                setTimeout(() => window.location.reload(), 600);
            } else {
                alert(data.error || 'Error deleting questions.');
            }
        } catch (err) {
            alert('Error connecting to server.');
        }
    }

    // ==========================================
    // Import into Examination Modal
    // ==========================================
    let pendingImportIds = [];

    function openImportModal(questionIds) {
        pendingImportIds = questionIds;
        if (!elements.modalImport) return;
        if (elements.importCountText) {
            elements.importCountText.textContent = `${questionIds.length} question(s) selected`;
        }
        elements.modalImport.style.display = 'flex';
    }

    async function handleConfirmImport() {
        const examId = elements.importTargetExam?.value;
        if (!examId) {
            alert('Please select a target examination.');
            return;
        }

        try {
            elements.btnConfirmImport.disabled = true;
            elements.btnConfirmImport.textContent = 'Importing...';

            const res = await fetch('/admin/question-bank/import-to-exam', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': elements.csrfToken,
                },
                body: JSON.stringify({
                    csrf_token: elements.csrfToken,
                    question_ids: pendingImportIds,
                    exam_id: parseInt(examId, 10),
                }),
            });

            const data = await res.json();
            if (res.ok && data.success) {
                if (elements.modalImport) elements.modalImport.style.display = 'none';
                showAlert(data.message || 'Questions imported into examination successfully!', 'success');
                selectedQuestionIds.clear();
                updateBulkBar();
            } else {
                alert(data.error || 'Import failed.');
            }
        } catch (err) {
            alert('Error connecting to server.');
        } finally {
            elements.btnConfirmImport.disabled = false;
            elements.btnConfirmImport.textContent = 'Confirm Import';
        }
    }

    // ==========================================
    // Helpers
    // ==========================================
    function getTypeLabel(type) {
        const map = {
            mcq: 'MCQ',
            multiple_select: 'MSQ (Multi-Select)',
            true_false: 'True / False',
            fill_blank: 'Fill in Blanks',
            short_answer: 'Short Answer',
            long_answer: 'Long Answer',
            numerical: 'Numerical',
        };
        return map[type] || (type || 'MCQ').toUpperCase();
    }

    function getDifficultyClass(diff) {
        const d = (diff || '').toLowerCase();
        if (d === 'easy') return 'diff-easy';
        if (d === 'hard') return 'diff-hard';
        return 'diff-medium';
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function showAlert(msg, level = 'info') {
        if (!elements.alertBanner) return;
        elements.alertBanner.textContent = msg;
        elements.alertBanner.className = `alert-banner alert-${level}`;
        elements.alertBanner.style.display = 'block';
        elements.alertBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

})();
