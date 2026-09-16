/*
 * SPC 누적 관리도 (§3-4).
 * /api/spc/series 에서 JSON 을 받아 Chart.js 로 렌더.
 * 회색 음영 = baseline · 파란선 = 원값 · 주황선 = EWMA · 파선 = UCL · 실선 = CENTER · 붉은 점 = 알람.
 */
(function () {
    "use strict";

    var config = window.SPC_CONFIG || {};
    var apiUrl = config.apiUrl;
    var baselineSize = config.baselineSize || 30;
    var canvas = document.getElementById("spcChart");
    if (canvas === null || apiUrl === undefined) {
        return;
    }

    fetch(apiUrl, { credentials: "same-origin" })
        .then(function (response) {
            if (response.ok !== true) {
                throw new Error("SPC API 오류 status=" + response.status);
            }
            return response.json();
        })
        .then(function (data) {
            renderChart(data);
        })
        .catch(function (err) {
            var context = canvas.getContext("2d");
            context.fillStyle = "#b91c1c";
            context.font = "13px sans-serif";
            context.fillText("SPC 시계열 로드 실패: " + err.message, 10, 30);
        });

    function renderChart(data) {
        var points = data.points || [];
        var labels = [];
        var values = [];
        var ewma = [];
        var alarms = [];
        var centerLine = [];
        var uclLine = [];

        var summary = data.summary || {};
        var centerFinal = summary.center;
        var uclFinal = summary.ucl;

        var index = 0;
        while (index < points.length) {
            var p = points[index];
            labels.push(String(p.seq_no));
            values.push(p.value);
            ewma.push(p.ewma);

            if (p.is_alarm === 1) {
                alarms.push({ x: index, y: p.value });
            }

            centerLine.push(p.center !== null ? p.center : null);
            uclLine.push(p.ucl !== null ? p.ucl : null);
            index = index + 1;
        }

        var chartContext = canvas.getContext("2d");

        new Chart(chartContext, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "value",
                        data: values,
                        borderColor: "#2563eb",
                        backgroundColor: "rgba(37, 99, 235, 0.15)",
                        borderWidth: 1.5,
                        pointRadius: 2,
                        tension: 0
                    },
                    {
                        label: "EWMA",
                        data: ewma,
                        borderColor: "#f59e0b",
                        borderWidth: 1.5,
                        pointRadius: 0,
                        tension: 0,
                        spanGaps: true
                    },
                    {
                        label: "CENTER",
                        data: centerLine,
                        borderColor: "#10b981",
                        borderWidth: 1,
                        pointRadius: 0,
                        borderDash: [],
                        tension: 0,
                        spanGaps: true
                    },
                    {
                        label: "UCL",
                        data: uclLine,
                        borderColor: "#ef4444",
                        borderWidth: 1,
                        pointRadius: 0,
                        borderDash: [6, 3],
                        tension: 0,
                        spanGaps: true
                    },
                    {
                        label: "알람",
                        type: "scatter",
                        data: alarms,
                        borderColor: "#ef4444",
                        backgroundColor: "#ef4444",
                        pointRadius: 5,
                        showLine: false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "top" },
                    tooltip: { enabled: true }
                },
                scales: {
                    x: {
                        title: { display: true, text: "seq_no" }
                    },
                    y: {
                        title: { display: true, text: "value" },
                        beginAtZero: true
                    }
                },
                animation: false
            },
            plugins: [{
                id: "baselineShade",
                beforeDatasetsDraw: function (chart) {
                    var xAxis = chart.scales.x;
                    var yAxis = chart.scales.y;
                    if (xAxis === undefined || yAxis === undefined) return;

                    var lastBaselineIndex = baselineSize - 1;
                    if (lastBaselineIndex >= labels.length) {
                        lastBaselineIndex = labels.length - 1;
                    }
                    if (lastBaselineIndex < 0) return;

                    var leftX = xAxis.getPixelForValue(0);
                    var rightX = xAxis.getPixelForValue(lastBaselineIndex);
                    var ctx = chart.ctx;
                    ctx.save();
                    ctx.fillStyle = "rgba(156, 163, 175, 0.14)";
                    ctx.fillRect(leftX, yAxis.top, rightX - leftX, yAxis.bottom - yAxis.top);
                    ctx.restore();
                }
            }]
        });
    }
})();
