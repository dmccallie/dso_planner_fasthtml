// static/js/dso_chart_htmx.js
// D3 chart that integrates with HTMX events

// Listen for HTMX afterSwap event to initialize chart
document.addEventListener('htmx:afterSwap', function(event) {
    const target = event.detail.target;
    
    // Check if the swapped content contains our chart container
    const chartContainer = target.querySelector('#chart-container') || 
                          (target.id === 'chart-container' ? target : null);
    
    if (chartContainer) {
        initializeChart(chartContainer);
    }
});

// Also listen for custom chartInit event (from hyperscript)
document.addEventListener('chartInit', function(event) {
    const chartContainer = event.target;
    if (chartContainer && chartContainer.id === 'chart-container') {
        initializeChart(chartContainer);
    }
});

async function initializeChart(container) {
    // Read configuration from data attributes
    const dsoId = container.dataset.dsoId;
    const lat = parseFloat(container.dataset.lat);
    const lon = parseFloat(container.dataset.lon);
    const date = container.dataset.date;
    
    // Show loading state
    container.innerHTML = '<div class="loading">Loading chart data...</div>';
    
    try {
        // Fetch data from API
        const queryParams = new URLSearchParams({ lat, lon, date });
        const apiUrl = `/api/dso/${dsoId}/positions?${queryParams}`;
        
        const response = await fetch(apiUrl);
        
        if (!response.ok) {
            throw new Error(`API request failed: ${response.status}`);
        }
        
        const config = await response.json();
        config.width = 900;
        config.height = 600;
        
        // Clear loading and render
        container.innerHTML = '';
        renderChart(container, config);
        
        // Dispatch custom event that chart is ready (useful for other HTMX interactions)
        container.dispatchEvent(new CustomEvent('chartReady', { 
            bubbles: true,
            detail: { dsoId, config }
        }));
        
    } catch (error) {
        console.error('Error loading chart data:', error);
        container.innerHTML = `
            <div class="error">
                <h3>Error Loading Chart</h3>
                <p>${error.message}</p>
                <button onclick="location.reload()">Retry</button>
            </div>
        `;
    }
}

function renderChart(container, config) {
    const {
        data,
        dso_name,
        obs_lat,
        obs_long,
        obs_date,
        observer_hours,
        safe_alt = 20,
        width = 900,
        height = 600
    } = config;

    // Parse data
    const parsedData = data.map(d => ({
        ...d,
        time: new Date(d.time)
    }));

    const margin = { top: 35, bottom: 50, left: 50, right: 50 };
    const innerHeight = height - margin.top - margin.bottom;
    const innerWidth = width - margin.left - margin.right;

    // Create SVG
    const svg = d3.select(container)
        .append('svg')
        .attr('width', width)
        .attr('height', height)
        .attr('viewBox', `0 0 ${width} ${height}`)
        .style('background-color', 'black')
        .style('max-width', '100%')
        .style('height', 'auto');

    const g = svg.append('g')
        .attr('transform', `translate(${margin.left},${margin.top})`);

    // Scales
    const xScaleDate = d3.scaleTime()
        .domain(d3.extent(parsedData, d => d.time))
        .range([0, innerWidth]);

    const xScale = d3.scaleLinear()
        .domain(d3.extent(parsedData, d => d.hour))
        .range([0, innerWidth]);

    const yScale = d3.scaleLinear()
        .domain(d3.extent(parsedData, d => d.alt))
        .range([innerHeight, 0])
        .nice();

    // Axes
    const xAxis = d3.axisBottom(xScaleDate)
        .ticks(d3.timeHour.every(2))
        .tickFormat(d3.timeFormat('%H:%M'));

    const yAxis = d3.axisLeft(yScale);
    const foreColor = 'rgba(200, 200, 200, 0.8)';

    // Draw axes
    g.append('g')
        .attr('class', 'y-axis')
        .call(yAxis)
        .call(g => g.selectAll('line').attr('stroke', foreColor))
        .call(g => g.selectAll('path').attr('stroke', foreColor))
        .call(g => g.selectAll('text').attr('fill', foreColor));

    g.append('g')
        .attr('class', 'x-axis')
        .attr('transform', `translate(0,${innerHeight + 5})`)
        .call(xAxis)
        .call(g => g.selectAll('line').attr('stroke', foreColor))
        .call(g => g.selectAll('path').attr('stroke', foreColor))
        .call(g => g.selectAll('text').attr('fill', foreColor));

    // Y axis label
    g.append('text')
        .attr('transform', `translate(${-30},${innerHeight / 2}) rotate(-90)`)
        .attr('text-anchor', 'middle')
        .attr('fill', foreColor)
        .style('font-weight', 100)
        .text('Altitude');

    // X axis label
    const observingDate = parsedData[0].time.toLocaleDateString();
    const lastDate = parsedData[parsedData.length - 1].time;
    const xaxisLabel = lastDate.getDate() !== parsedData[0].time.getDate()
        ? `${observingDate} - ${lastDate.toLocaleDateString()}`
        : observingDate;

    g.append('text')
        .attr('x', innerWidth / 2)
        .attr('y', innerHeight + 40)
        .attr('text-anchor', 'middle')
        .attr('fill', foreColor)
        .style('font-weight', 100)
        .text(`Time (hours) ${xaxisLabel}`);

    // Line generator
    const lineGenerator = d3.line()
        .x(d => xScale(d.hour))
        .y(d => yScale(d.alt))
        .curve(d3.curveNatural);

    // Draw altitude curve
    g.append('path')
        .datum(parsedData)
        .attr('d', lineGenerator)
        .attr('fill', 'none')
        .attr('stroke', foreColor)
        .attr('stroke-width', 2)
        .attr('stroke-linecap', 'round');

    // Data points with labels
    const points = g.selectAll('.data-point')
        .data(parsedData)
        .enter()
        .append('g')
        .attr('class', 'data-point');

    points.append('circle')
        .attr('cx', d => xScale(d.hour))
        .attr('cy', d => yScale(d.alt))
        .attr('r', 5)
        .attr('fill', 'red');

    points.append('text')
        .attr('x', d => xScale(d.hour))
        .attr('y', d => yScale(d.alt))
        .attr('dy', -15)
        .attr('text-anchor', 'middle')
        .attr('fill', foreColor)
        .attr('stroke', foreColor)
        .attr('stroke-width', 0.5)
        .style('font-size', '14px')
        .text(d => `${Math.round(d.alt)}° / ${Math.round(d.azi)}°`);

    // Vertical grid lines
    xScale.ticks().forEach(tickValue => {
        g.append('line')
            .attr('x1', xScale(tickValue))
            .attr('x2', xScale(tickValue))
            .attr('y1', 0)
            .attr('y2', innerHeight)
            .attr('stroke', foreColor)
            .attr('opacity', 0.5);
    });

    // Sun times
    const obsDateObj = new Date(obs_date);
    const sunTimes = SunCalc.getTimes(obsDateObj, obs_lat, obs_long);
    const darkToday = sunTimes.dusk;
    
    const tomorrow = new Date(obsDateObj);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const sunTimesTomorrow = SunCalc.getTimes(tomorrow, obs_lat, obs_long);
    const lightTomorrow = sunTimesTomorrow.dawn;

    // Markers
    drawVerticalMarker(g, xScaleDate(darkToday), innerHeight, 'blue', 
        `Dusk     ${darkToday.toLocaleTimeString()}`);
    drawVerticalMarker(g, xScaleDate(lightTomorrow), innerHeight, 'blue',
        `Dawn     ${lightTomorrow.toLocaleTimeString()}`);

    // Safe altitude line
    g.append('line')
        .attr('x1', 0)
        .attr('x2', innerWidth)
        .attr('y1', yScale(safe_alt))
        .attr('y2', yScale(safe_alt))
        .attr('stroke', 'yellow')
        .attr('stroke-width', 0.5);

    // Observer hours
    const startHours = parseTimeString(observer_hours.start);
    const endHours = parseTimeString(observer_hours.end);
    const obsHoursStart = new Date(observingDate + ' ' + observer_hours.start);
    let obsHoursEnd = new Date(observingDate + ' ' + observer_hours.end);

    if (endHours < startHours) {
        obsHoursEnd.setDate(obsHoursEnd.getDate() + 1);
    }

    const rectWidth = xScaleDate(obsHoursEnd) - xScaleDate(obsHoursStart);
    g.append('rect')
        .attr('x', xScaleDate(obsHoursStart))
        .attr('y', 0)
        .attr('width', Math.max(0, rectWidth))
        .attr('height', innerHeight)
        .attr('fill', 'rgba(0, 255, 0, 0.15)');

    // Title
    svg.append('text')
        .attr('x', width / 2)
        .attr('y', 20)
        .attr('text-anchor', 'middle')
        .attr('fill', foreColor)
        .attr('stroke', foreColor)
        .attr('stroke-width', 0.5)
        .style('font-size', '22px')
        .text(dso_name);

    // NOW marker with updates
    let nowMarker = null;
    function updateNowMarker() {
        const timeNow = new Date();
        const scaledTimeNow = xScaleDate(timeNow);

        if (nowMarker) nowMarker.remove();

        nowMarker = g.append('g')
            .attr('class', 'now-marker')
            .attr('transform', `translate(${scaledTimeNow},0)`);

        nowMarker.append('line')
            .attr('y1', 10)
            .attr('y2', innerHeight)
            .attr('stroke', 'green')
            .attr('stroke-width', 2);

        nowMarker.append('text')
            .attr('transform', `translate(0,${innerHeight / 2}) rotate(-90)`)
            .attr('text-anchor', 'middle')
            .attr('dy', -3)
            .attr('fill', 'green')
            .attr('stroke', 'green')
            .style('font-size', 'large')
            .text(`NOW      ${timeNow.toLocaleTimeString()}`);
    }

    updateNowMarker();
    const intervalId = setInterval(updateNowMarker, 60000);
    
    // Clean up interval when container is removed (HTMX swap)
    const observer = new MutationObserver((mutations) => {
        if (!document.body.contains(container)) {
            clearInterval(intervalId);
            observer.disconnect();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
}

function drawVerticalMarker(g, x, height, color, label) {
    const marker = g.append('g')
        .attr('transform', `translate(${x},0)`);

    marker.append('line')
        .attr('y1', 10)
        .attr('y2', height)
        .attr('stroke', color)
        .attr('stroke-width', 1);

    marker.append('text')
        .attr('transform', `translate(0,${height / 2}) rotate(-90)`)
        .attr('text-anchor', 'middle')
        .attr('dy', -3)
        .attr('fill', color)
        .attr('stroke', color)
        .style('font-size', 'large')
        .text(label);
}

function parseTimeString(timeStr) {
    const [hours, minutes] = timeStr.split(':').map(Number);
    return hours + minutes / 60;
}