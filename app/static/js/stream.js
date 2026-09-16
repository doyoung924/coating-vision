/*
 * stream.js — 인라인 스트림 재생
 *
 * 동작 로직 (평문):
 *  1. 페이지 로드시 초기 시퀀스의 JSON 을 /api/sequence/<id> 로 받아 클라이언트에 통째로 담는다.
 *  2. Chart.js 로 defect_ratio · EWMA · UCL · center 를 준비하되, 데이터는 현재 인덱스까지만 노출한다.
 *  3. 재생 버튼 → setInterval 로 현재 인덱스를 1씩 증가. 이미지·박스·상태·차트가 함께 갱신된다.
 *  4. 슬라이더는 임의 이동, 속도 버튼은 setInterval 주기 변경.
 *  5. 현재 프레임의 alerts 가 비어있지 않으면 프레임 경계를 빨갛게 하고 Advisor 로그에 한 줄 추가한다.
 *  6. 다음 3장은 <link rel="preload"> 대신 Image() 로 프리로드해 깜빡임 방지.
 */

const IMAGE_BASE = "/frame/";
const API_BASE = "/api/sequence/";
const IMAGE_WIDTH = 640;
const IMAGE_HEIGHT = 480;
const CROP_SRC = 128;
const CROP_DEST = 512;

const SPEED_INTERVAL = {
    "0.5": 400,
    "1": 200,
    "2": 100,
    "max": 30,
};

let currentSeq = null;      // {sequence_id, frames, ...}
let currentIndex = 0;
let intervalHandle = null;
let currentSpeed = "1";
let alertCount = 0;
let chartInstance = null;
let lastLoggedIndex = -1;   // Advisor 로그 중복 삽입 방지 (인덱스 후진 시 재설정)
let currentTopDetection = null;
let lastDetectionState = null;  // 재생 중 잔상 유지용 {index, frame, detection}
const cropImage = new Image();
cropImage.onload = function () {
    drawCropIfReady();
};

const el = {
    cropCanvas: document.getElementById("cropCanvas"),
    cropNote: document.getElementById("cropNote"),
    cropStatus: document.getElementById("cropStatus"),
    chartNoteIncapable: document.getElementById("chartNoteIncapable"),
    chartNoteUcl: document.getElementById("chartNoteUcl"),
    seqSelect: document.getElementById("seqSelect"),
    btnPlay: document.getElementById("btnPlay"),
    btnPause: document.getElementById("btnPause"),
    btnReset: document.getElementById("btnReset"),
    speedButtons: document.getElementById("speedButtons"),
    slider: document.getElementById("frameSlider"),
    counter: document.getElementById("frameCounter"),
    frameFrame: document.getElementById("frameFrame"),
    frameImage: document.getElementById("frameImage"),
    overlay: document.getElementById("overlay"),
    alertBanner: document.getElementById("alertBanner"),
    statFrame: document.getElementById("statFrame"),
    statImage: document.getElementById("statImage"),
    statDefectRatio: document.getElementById("statDefectRatio"),
    statEwma: document.getElementById("statEwma"),
    statUcl: document.getElementById("statUcl"),
    statCenter: document.getElementById("statCenter"),
    statBoxCount: document.getElementById("statBoxCount"),
    statLabels: document.getElementById("statLabels"),
    statCapability: document.getElementById("statCapability"),
    statAlertCount: document.getElementById("statAlertCount"),
    statVerdict: document.getElementById("statVerdict"),
    chartCanvas: document.getElementById("controlChart"),
    advisorLog: document.getElementById("advisorLog"),
};


function initChart() {
    const ctx = el.chartCanvas.getContext("2d");
    chartInstance = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    label: "defect_ratio",
                    data: [],
                    borderColor: "#2563eb",
                    backgroundColor: "rgba(37, 99, 235, 0.10)",
                    borderWidth: 1.5,
                    pointRadius: 0,
                    tension: 0.05,
                },
                {
                    label: "EWMA",
                    data: [],
                    borderColor: "#059669",
                    borderWidth: 1.5,
                    pointRadius: 0,
                    tension: 0.05,
                },
                {
                    label: "UCL",
                    data: [],
                    borderColor: "#dc2626",
                    borderDash: [8, 4],
                    borderWidth: 3,
                    pointRadius: 0,
                },
                {
                    label: "center",
                    data: [],
                    borderColor: "#9ca3af",
                    borderDash: [2, 4],
                    borderWidth: 1,
                    pointRadius: 0,
                },
                {
                    label: "alert",
                    data: [],
                    borderColor: "rgba(239, 68, 68, 0)",
                    backgroundColor: "#ef4444",
                    borderWidth: 0,
                    showLine: false,
                    pointRadius: 4,
                    pointHoverRadius: 5,
                },
                {
                    label: "현재",
                    data: [],
                    borderColor: "#111827",
                    backgroundColor: "#111827",
                    borderWidth: 0,
                    showLine: false,
                    pointRadius: 5,
                    pointStyle: "rectRot",
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: {
                mode: "nearest",
                intersect: false,
            },
            plugins: {
                legend: {
                    position: "top",
                    labels: {
                        boxWidth: 12,
                        font: {size: 11},
                    },
                },
                tooltip: {
                    enabled: true,
                },
            },
            scales: {
                x: {
                    type: "linear",
                    title: {display: true, text: "index (프레임 순서)"},
                    ticks: {font: {size: 10}},
                    min: 0,
                    max: 100,
                },
                y: {
                    min: 0,
                    max: 1.05,
                    title: {display: true, text: "결함 비율"},
                    ticks: {font: {size: 10}},
                },
            },
        },
    });
}


function loadSequence(seqId) {
    stopPlayback();
    fetch(API_BASE + seqId)
        .then(function (response) {
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            return response.json();
        })
        .then(function (payload) {
            currentSeq = payload;
            currentIndex = 0;
            alertCount = 0;
            lastDetectionState = null;
            el.slider.max = String(payload.n_frames - 1);
            el.slider.value = "0";
            el.statCapability.innerHTML = "<span class=\"cap-badge cap-" + payload.capability + "\" title=\"" + VERDICT_BADGE_TOOLTIP + "\">" + payload.capability + "</span>";
            resetChart(payload);
            renderFrame(0);
        })
        .catch(function (err) {
            console.error("시퀀스 로드 실패", err);
        });
}


function resetChart(payload) {
    // 축은 시퀀스 길이에 맞춰 고정. 재생하며 데이터가 왼→오로 채워진다.
    chartInstance.options.scales.x.min = 0;
    chartInstance.options.scales.x.max = payload.n_frames - 1;
    // UCL/center 는 지평선. 두 점만 있으면 된다.
    const uclData = [
        {x: 0, y: payload.ucl},
        {x: payload.n_frames - 1, y: payload.ucl},
    ];
    const centerData = [
        {x: 0, y: payload.center},
        {x: payload.n_frames - 1, y: payload.center},
    ];
    chartInstance.data.datasets[0].data = [];
    chartInstance.data.datasets[1].data = [];
    chartInstance.data.datasets[2].data = uclData;
    chartInstance.data.datasets[3].data = centerData;
    chartInstance.data.datasets[4].data = [];
    chartInstance.data.datasets[5].data = [];
    chartInstance.update("none");
    updateIncapableNote(payload);
}


function updateIncapableNote(payload) {
    // INCAPABLE 시퀀스일 때만 UCL 이 y축 상한 근접함을 알리는 배너를 띄운다.
    // 배너 문구는 templates/stream.html 의 #chartNoteIncapable 에 정의되어 있으며
    // chart-note-caveat 슬롯에 "판정 자체는 임의 임계 0.25 (10_spc_tuning.py:46) 기반"
    // 이 한 줄을 이미 포함한다. 여기서는 UCL 수치만 채우고 표시/숨김을 토글한다.
    if (payload.capability === "INCAPABLE") {
        el.chartNoteUcl.textContent = payload.ucl.toFixed(3);
        el.chartNoteIncapable.classList.remove("hidden");
    } else {
        el.chartNoteIncapable.classList.add("hidden");
    }
}


// 판정 배지에 붙는 근거 부재 툴팁. GOOD/MARGINAL/INCAPABLE 세 배지 모두
// 임의 임계 0.25 (10_spc_tuning.py:46, experiment_log.md §9-4 '임의값이다') 기반이라
// 절대 수치가 아니라 상대 순위로만 해석해야 한다는 사실을 마우스 hover 로 노출.
const VERDICT_BADGE_TOOLTIP = "임의 임계 0.25 (10_spc_tuning.py:46) 기반 판정 — 근거 부재. 절대 수치가 아닌 상대 순위로만 해석.";
const ALARM_BADGE_TOOLTIP = "관리도 UCL 이탈. 이탈 판정 자체가 임의 임계 0.25 기반 관리한계 위에서 계산된 결과.";


function updateChartUpTo(index) {
    // 현재 인덱스까지만 defect_ratio, EWMA, 알람 마커를 노출.
    if (!currentSeq) {
        return;
    }
    const defectData = [];
    const ewmaData = [];
    const alertData = [];
    for (let i = 0; i <= index; i = i + 1) {
        const frame = currentSeq.frames[i];
        defectData.push({x: i, y: frame.defect_ratio});
        ewmaData.push({x: i, y: frame.ewma});
        if (frame.alerts && frame.alerts.length > 0) {
            alertData.push({x: i, y: frame.defect_ratio});
        }
    }
    chartInstance.data.datasets[0].data = defectData;
    chartInstance.data.datasets[1].data = ewmaData;
    chartInstance.data.datasets[4].data = alertData;
    chartInstance.data.datasets[5].data = [{x: index, y: currentSeq.frames[index].defect_ratio}];
    chartInstance.update("none");
}


function countAlertsUpTo(index) {
    let count = 0;
    for (let i = 0; i <= index; i = i + 1) {
        const frame = currentSeq.frames[i];
        if (frame.alerts && frame.alerts.length > 0) {
            count = count + 1;
        }
    }
    return count;
}


function renderFrame(index) {
    if (!currentSeq) {
        return;
    }
    if (index < 0) {
        index = 0;
    }
    if (index >= currentSeq.n_frames) {
        index = currentSeq.n_frames - 1;
        stopPlayback();
    }
    currentIndex = index;
    const frame = currentSeq.frames[index];

    el.frameImage.src = IMAGE_BASE + frame.image;
    el.slider.value = String(index);
    el.counter.textContent = (index + 1) + " / " + currentSeq.n_frames;

    el.statFrame.textContent = frame.frame_number + " (patch " + frame.patch_idx + ")";
    el.statImage.textContent = frame.image;
    el.statDefectRatio.textContent = formatNumber(frame.defect_ratio, 4);
    el.statEwma.textContent = formatNumber(frame.ewma, 4);
    el.statUcl.textContent = formatNumber(currentSeq.ucl, 4);
    el.statCenter.textContent = formatNumber(currentSeq.center, 4);
    el.statBoxCount.textContent = String(frame.detections.length);
    el.statLabels.innerHTML = renderLabelsHTML(frame.labels, frame.detections.length > 0);

    updateOverlay(frame.detections);
    updateCropTarget(frame, index);
    updateAlert(frame, index);
    updateChartUpTo(index);
    updateAlertCount(index);
    preloadNext(index);
}


function renderLabelsHTML(labels, hasDetection) {
    // labels.csv 의 정답 라벨과, 그것이 본 시스템의 검출 대상(핀홀)에 대해
    // 어떤 관계인지 배지로 함께 보여준다.
    const parts = [];
    if (labels.surface_crack === 1) {
        parts.push("crack");
    }
    if (labels.delamination === 1) {
        parts.push("delam");
    }
    if (labels.pinhole === 1) {
        parts.push("pinhole");
    }
    let text;
    if (parts.length === 0) {
        text = "clean";
    } else {
        text = parts.join(" · ");
    }
    let html = text + " <span class=\"label-src\">(labels.csv)</span>";

    if (labels.pinhole === 1 && !hasDetection) {
        html = html + " <span class=\"gt-badge gt-badge-miss\">미검출</span>";
    } else if (labels.pinhole === 0 && (labels.surface_crack === 1 || labels.delamination === 1)) {
        html = html + " <span class=\"gt-badge gt-badge-nontarget\">검출 대상 아님</span>";
    }
    return html;
}


function updateCropTarget(frame, currentIndex) {
    // 현재 프레임에 검출이 있으면 그것을 그린다.
    // 없으면 직전 검출을 흐리게 유지한다 (재생 중 잔상 유지).
    if (frame.detections && frame.detections.length > 0) {
        let top = frame.detections[0];
        for (let i = 1; i < frame.detections.length; i = i + 1) {
            if (frame.detections[i].conf > top.conf) {
                top = frame.detections[i];
            }
        }
        currentTopDetection = top;
        lastDetectionState = {
            index: currentIndex,
            frame: frame,
            detection: top,
        };
        el.cropCanvas.classList.remove("crop-stale");
        el.cropStatus.textContent = "현재 프레임";
        cropImage.src = IMAGE_BASE + frame.image;
        if (cropImage.complete && cropImage.naturalWidth > 0) {
            drawCropIfReady();
        }
        return;
    }

    // 검출이 없다. 이 시퀀스에서 아직 한 번도 검출이 없었으면 placeholder.
    if (lastDetectionState === null) {
        currentTopDetection = null;
        el.cropCanvas.classList.remove("crop-stale");
        el.cropStatus.textContent = "";
        clearCropCanvas("검출 없음");
        return;
    }

    // 직전 검출을 흐리게(opacity 0.6) 유지.
    currentTopDetection = lastDetectionState.detection;
    const gap = currentIndex - lastDetectionState.index;
    el.cropCanvas.classList.add("crop-stale");
    el.cropStatus.textContent = "직전 검출 · 프레임 "
        + lastDetectionState.frame.frame_number
        + " (" + gap + "프레임 전)";
    cropImage.src = IMAGE_BASE + lastDetectionState.frame.image;
    if (cropImage.complete && cropImage.naturalWidth > 0) {
        drawCropIfReady();
    }
}


function drawCropIfReady() {
    if (currentTopDetection === null) {
        return;
    }
    if (!cropImage.complete || cropImage.naturalWidth === 0) {
        return;
    }
    const detection = currentTopDetection;
    const context = el.cropCanvas.getContext("2d");
    context.clearRect(0, 0, CROP_DEST, CROP_DEST);

    // 박스 중심에서 128×128 크롭. 이미지 경계에 물릴 때는 클램프.
    const centerX = (detection.x1 + detection.x2) / 2.0;
    const centerY = (detection.y1 + detection.y2) / 2.0;
    let sourceX = Math.round(centerX - CROP_SRC / 2);
    let sourceY = Math.round(centerY - CROP_SRC / 2);
    if (sourceX < 0) {
        sourceX = 0;
    }
    if (sourceY < 0) {
        sourceY = 0;
    }
    if (sourceX + CROP_SRC > IMAGE_WIDTH) {
        sourceX = IMAGE_WIDTH - CROP_SRC;
    }
    if (sourceY + CROP_SRC > IMAGE_HEIGHT) {
        sourceY = IMAGE_HEIGHT - CROP_SRC;
    }

    context.imageSmoothingEnabled = true;
    context.drawImage(
        cropImage,
        sourceX, sourceY, CROP_SRC, CROP_SRC,
        0, 0, CROP_DEST, CROP_DEST,
    );

    // 크롭 좌표계에서 박스 그리기 (4× 확대)
    const scale = CROP_DEST / CROP_SRC;
    const boxX = (detection.x1 - sourceX) * scale;
    const boxY = (detection.y1 - sourceY) * scale;
    const boxW = (detection.x2 - detection.x1) * scale;
    const boxH = (detection.y2 - detection.y1) * scale;
    context.strokeStyle = "#ffffff";
    context.lineWidth = 6;
    context.strokeRect(boxX, boxY, boxW, boxH);
    context.strokeStyle = "#ef4444";
    context.lineWidth = 3;
    context.strokeRect(boxX, boxY, boxW, boxH);

    // 라벨
    const label = "conf " + detection.conf.toFixed(2) + " · aspect " +
        (Math.max(detection.x2 - detection.x1, detection.y2 - detection.y1) /
         Math.max(1, Math.min(detection.x2 - detection.x1, detection.y2 - detection.y1))).toFixed(2);
    context.font = "bold 20px sans-serif";
    const metrics = context.measureText(label);
    context.fillStyle = "#ef4444";
    context.fillRect(4, 4, metrics.width + 16, 32);
    context.fillStyle = "#ffffff";
    context.fillText(label, 12, 26);

    el.cropNote.textContent = "";
}


function clearCropCanvas(note) {
    const context = el.cropCanvas.getContext("2d");
    context.clearRect(0, 0, CROP_DEST, CROP_DEST);
    context.fillStyle = "#f4f5f7";
    context.fillRect(0, 0, CROP_DEST, CROP_DEST);
    context.fillStyle = "#9ca3af";
    context.font = "20px sans-serif";
    const metrics = context.measureText(note);
    context.fillText(note, (CROP_DEST - metrics.width) / 2, CROP_DEST / 2);
    el.cropNote.textContent = "";
}


function updateAlertCount(index) {
    alertCount = countAlertsUpTo(index);
    el.statAlertCount.textContent = String(alertCount);
}


function updateOverlay(detections) {
    el.overlay.innerHTML = "";
    if (!detections || detections.length === 0) {
        return;
    }
    const imgWidth = el.frameImage.clientWidth || IMAGE_WIDTH;
    const imgHeight = el.frameImage.clientHeight || IMAGE_HEIGHT;
    const scaleX = imgWidth / IMAGE_WIDTH;
    const scaleY = imgHeight / IMAGE_HEIGHT;
    for (let i = 0; i < detections.length; i = i + 1) {
        const det = detections[i];
        const box = document.createElement("div");
        box.className = "det-box";
        box.style.left = (det.x1 * scaleX) + "px";
        box.style.top = (det.y1 * scaleY) + "px";
        box.style.width = ((det.x2 - det.x1) * scaleX) + "px";
        box.style.height = ((det.y2 - det.y1) * scaleY) + "px";
        const label = document.createElement("span");
        label.className = "det-box-label";
        label.textContent = "p=" + det.conf.toFixed(2);
        box.appendChild(label);
        el.overlay.appendChild(box);
    }
}


function updateAlert(frame, index) {
    const hasAlert = frame.alerts && frame.alerts.length > 0;
    if (hasAlert) {
        el.frameFrame.classList.add("alert-active");
        el.alertBanner.classList.remove("hidden");
        el.statVerdict.innerHTML = "<span class=\"cap-badge cap-INCAPABLE\" title=\"" + ALARM_BADGE_TOOLTIP + "\">ALARM</span>";
    } else {
        el.frameFrame.classList.remove("alert-active");
        el.alertBanner.classList.add("hidden");
        if (frame.defect_ratio !== null && currentSeq && frame.defect_ratio > currentSeq.center) {
            el.statVerdict.innerHTML = "<span class=\"cap-badge cap-MARGINAL\" title=\"" + VERDICT_BADGE_TOOLTIP + "\">WARN</span>";
        } else {
            el.statVerdict.innerHTML = "<span class=\"cap-badge cap-GOOD\" title=\"" + VERDICT_BADGE_TOOLTIP + "\">OK</span>";
        }
    }
    rebuildAdvisorLog(index);
}


function rebuildAdvisorLog(index) {
    // 인덱스 0..index 범위의 알람 프레임을 최신순으로 다시 그린다.
    // 슬라이더 후진 시에도 로그가 일관되게 유지된다.
    el.advisorLog.innerHTML = "";
    const items = [];
    for (let i = 0; i <= index; i = i + 1) {
        const frame = currentSeq.frames[i];
        if (frame.alerts && frame.alerts.length > 0) {
            items.push({index: i, frame: frame});
        }
    }
    if (items.length === 0) {
        el.advisorLog.innerHTML = "<li class=\"advisor-log-empty\">아직 알람 없음</li>";
        return;
    }
    // 최신순: 마지막에 삽입한 것이 맨 위
    for (let k = 0; k < items.length; k = k + 1) {
        const entry = items[k];
        const rules = [];
        for (let j = 0; j < entry.frame.alerts.length; j = j + 1) {
            rules.push(entry.frame.alerts[j].rule);
        }
        const unique = Array.from(new Set(rules)).join(", ");
        const li = document.createElement("li");
        li.className = "advisor-log-item";
        li.innerHTML =
            "<span class=\"log-index\">#" + (entry.index + 1) + "</span>" +
            " · idx " + entry.index +
            " · frame " + entry.frame.frame_number +
            " · defect_ratio " + formatNumber(entry.frame.defect_ratio, 4) +
            " &gt; UCL " + formatNumber(currentSeq.ucl, 4) +
            " · 규칙: <strong>" + unique + "</strong>";
        el.advisorLog.insertBefore(li, el.advisorLog.firstChild);
    }
    // 표시는 최대 40개까지
    while (el.advisorLog.children.length > 40) {
        el.advisorLog.removeChild(el.advisorLog.lastChild);
    }
}


function resetAdvisorLog() {
    el.advisorLog.innerHTML = "<li class=\"advisor-log-empty\">아직 알람 없음</li>";
}


function preloadNext(index) {
    if (!currentSeq) {
        return;
    }
    for (let i = 1; i <= 3; i = i + 1) {
        const nextIndex = index + i;
        if (nextIndex >= currentSeq.n_frames) {
            break;
        }
        const image = new Image();
        image.src = IMAGE_BASE + currentSeq.frames[nextIndex].image;
    }
}


function labelBadges(labels) {
    const parts = [];
    if (labels.surface_crack === 1) {
        parts.push("crack");
    }
    if (labels.delamination === 1) {
        parts.push("delam");
    }
    if (labels.pinhole === 1) {
        parts.push("pinhole");
    }
    if (parts.length === 0) {
        return "clean";
    }
    return parts.join(" · ");
}


function formatNumber(value, digits) {
    if (value === null || value === undefined) {
        return "—";
    }
    return value.toFixed(digits);
}


// --- 재생 컨트롤 ---

function startPlayback() {
    if (intervalHandle !== null) {
        return;
    }
    const delay = SPEED_INTERVAL[currentSpeed] || 200;
    el.btnPlay.disabled = true;
    el.btnPause.disabled = false;
    intervalHandle = setInterval(function () {
        renderFrame(currentIndex + 1);
    }, delay);
}


function stopPlayback() {
    if (intervalHandle !== null) {
        clearInterval(intervalHandle);
        intervalHandle = null;
    }
    el.btnPlay.disabled = false;
    el.btnPause.disabled = true;
}


function restartIfPlaying() {
    if (intervalHandle !== null) {
        stopPlayback();
        startPlayback();
    }
}


// --- 이벤트 바인딩 ---

function bindEvents() {
    el.seqSelect.addEventListener("change", function () {
        loadSequence(el.seqSelect.value);
    });
    el.btnPlay.addEventListener("click", startPlayback);
    el.btnPause.addEventListener("click", stopPlayback);
    el.btnReset.addEventListener("click", function () {
        stopPlayback();
        renderFrame(0);
    });
    el.slider.addEventListener("input", function () {
        renderFrame(parseInt(el.slider.value, 10));
    });
    const speedBtns = el.speedButtons.querySelectorAll("button");
    for (let i = 0; i < speedBtns.length; i = i + 1) {
        speedBtns[i].addEventListener("click", function () {
            const speed = this.getAttribute("data-speed");
            currentSpeed = speed;
            for (let j = 0; j < speedBtns.length; j = j + 1) {
                speedBtns[j].classList.remove("active");
            }
            this.classList.add("active");
            restartIfPlaying();
        });
    }
}


document.addEventListener("DOMContentLoaded", function () {
    initChart();
    bindEvents();
    if (el.seqSelect.value) {
        loadSequence(el.seqSelect.value);
    }
});
