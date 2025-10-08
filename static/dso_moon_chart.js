/**
 * DSO Moon Graph - Imperative D3 Version
 * Plots deep space object altitude and moon illumination over a year
 * sampled once a month at specified hour (default 9 PM standard time)
 */

// Initialize the chart from loading of script
export async function initDSOMoonChartFromAPI(containerId, dsoId, params = {}) {
    const container = d3.select(`#${containerId}`);
    
    // Show loading state
    container.html('<div class="loading">Loading DSO Moon chart data...</div>');

    const chart = new DSOYearlyGraph(containerId, {
        width: 1200,
        height: 800,
        safeAlt: 20
    });

    // Build API URL with query parameters
    const queryParams = new URLSearchParams({
        lat: params.lat || 38.9,
        lon: params.lon || -94.6,
        date: params.date || new Date().toISOString().split('T')[0],
        tz: params.tz || 'America/Chicago',
    });
    
    // Load data from API endpoint
    const apiURL = `/api/dso-moon-chart-data/${dsoId}/localization?${queryParams}`;
    fetch(apiURL)
        .then(response => response.json())
        .then(data => {
            //console.log("Fetched DSO moon chart data:", data);
            // Convert date strings to Date objects if needed
            const dsoData = data.dso_data.map(d => ({
                time: new Date(d.time),
                alt: d.alt,
                azi: d.azi
            }));
            // console.log(`DSO data points: ${dsoData.length}`, dsoData);
            const moonData = data.moon_data.map(d => ({
                time: new Date(d.time),
                illum: d.illum
            }));
            console.log(`Moon data points: ${moonData.length}`, moonData);

            // triggers the render()
            chart.setData(dsoData, moonData, data.dso_name);
        })
        .catch(error => {
            console.error('Error loading DSO moon chart data:', error);
            container.html(`<div class="error">Error loading chart data: ${error.message}</div>`);
        });
}

class DSOYearlyGraph {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.container = d3.select(`#${containerId}`);
        
        // Configuration
        this.config = {
            width: options.width || 900,
            height: options.height || 600,
            margin: { top: 35, bottom: 50, left: 50, right: 55 },
            safeAlt: options.safeAlt || 20,
            foreColor: options.foreColor || "rgba(200, 200, 200, 1)",
            backColor: options.backColor || "black",
            moonDataColor: options.moonDataColor || "rgb(163, 197, 208)"
        };
        
        // Calculate inner dimensions
        this.innerWidth = this.config.width - this.config.margin.left - this.config.margin.right;
        this.innerHeight = this.config.height - this.config.margin.top - this.config.margin.bottom;
        
        // Data storage
        this.dsoData = [];
        this.moonData = [];
        this.dsoName = "";
        
        // Initialize
        this.initSVG();
        this.initScales();
        this.initPathGenerators();
        this.startTimeUpdater();
    }
    
    initSVG() {
        // Clear any existing content
        this.container.selectAll("*").remove();
        
        // Create SVG
        this.svg = this.container
            .append("svg")
            .attr("class", "dso-chart")
            .attr("width", this.config.width)
            .attr("height", this.config.height)
            .attr("viewBox", `0 0 ${this.config.width} ${this.config.height}`)
            .style("background-color", this.config.backColor);
        
        // Create main group with margins
        this.g = this.svg
            .append("g")
            .attr("transform", `translate(${this.config.margin.left}, ${this.config.margin.top})`);
        
        // Create clip path
        this.g.append("defs")
            .append("clipPath")
            .attr("id", "cut-off-below-zero")
            .append("rect")
            .attr("x", 0)
            .attr("y", 0)
            .attr("width", this.innerWidth)
            .attr("height", this.innerHeight);
        
        // Create groups for different elements
        this.axisGroup = this.g.append("g").attr("class", "axes");
        this.pathsGroup = this.g.append("g").attr("class", "paths");
        this.labelsGroup = this.g.append("g").attr("class", "labels");
        this.pointsGroup = this.g.append("g").attr("class", "points");
        this.nowLineGroup = this.g.append("g").attr("class", "now-line");
        this.safeAltGroup = this.g.append("g").attr("class", "safe-alt-line");
        
        // Create title
        this.svg.append("text")
            .attr("class", "chart-title")
            .attr("text-anchor", "middle")
            .attr("x", this.config.width / 2)
            .attr("y", 20)
            .style("fill", this.config.foreColor)
            .style("stroke", this.config.foreColor)
            .style("stroke-width", "0.5")
            .style("font-size", "22px");
    }
    
    initScales() {
        // X scale (time)
        this.xScale = d3.scaleTime()
            .range([0, this.innerWidth]);
        
        // Y scale (altitude)
        this.yScale = d3.scaleLinear()
            .domain([0, 100])
            .range([this.innerHeight, 0])
            .nice();
        
        // Y scale for moon (right side)
        this.yMoonScale = d3.scaleLinear()
            .domain([0, 100])
            .range([this.innerHeight, this.innerHeight / 2])
            .nice();
    }
    
    initPathGenerators() {
        // DSO altitude path
        this.dsoPathLine = d3.line()
            .x(d => this.xScale(d.time))
            .y(d => this.yScale(d.alt))
            .curve(d3.curveNatural);
        
        // Moon illumination path
        this.moonPathLine = d3.line()
            .x(d => this.xScale(d.time))
            .y(d => this.yMoonScale(d.illum))
            .curve(d3.curveNatural);
    }
    
    setData(dsoData, moonData, dsoName) {
        this.dsoData = dsoData;
        this.moonData = moonData;
        this.dsoName = dsoName;
        
        // Update x scale domain based on data
        const timeExtent = d3.extent(dsoData, d => d.time);
        this.xScale.domain(timeExtent).nice();
        console.log(`Ready to render: X scale domain set to: ${timeExtent}`);

        this.render();
    }
    
    render() {
        if (this.dsoData.length === 0) return;
        
        this.renderAxes();
        this.renderAxisLabels();
        this.renderPaths();
        this.renderDataPoints();
        this.renderSafeAltLine();
        this.renderNowLine();
        this.updateTitle();
    }
    
    renderAxes() {
        this.axisGroup.selectAll("*").remove();
        
        // Left Y axis (altitude)
        const yAxis = d3.axisLeft(this.yScale);
        this.axisGroup.append("g")
            .attr("class", "y-axis-left")
            .call(yAxis)
            .call(g => g.selectAll("line, path")
                .style("stroke", this.config.foreColor))
            .call(g => g.selectAll("text")
                .style("fill", this.config.foreColor));
        
        // Right Y axis (moon illumination)
        const yMoonAxis = d3.axisRight(this.yMoonScale);
        this.axisGroup.append("g")
            .attr("class", "y-axis-right")
            .attr("transform", `translate(${this.innerWidth}, 0)`)
            .call(yMoonAxis)
            .call(g => g.selectAll("line, path")
                .style("stroke", this.config.moonDataColor))
            .call(g => g.selectAll("text")
                .style("fill", this.config.moonDataColor));
        
        // Bottom X axis (time)
        const xAxis = d3.axisBottom(this.xScale)
            .tickFormat(d3.timeFormat("%B")); // Month
        this.axisGroup.append("g")
            .attr("class", "x-axis")
            .attr("transform", `translate(0, ${this.innerHeight + 5})`)
            .call(xAxis)
            .call(g => g.selectAll("line, path")
                .style("stroke", this.config.foreColor))
            .call(g => g.selectAll("text")
                .style("fill", this.config.foreColor));
    }
    
    renderAxisLabels() {
        this.labelsGroup.selectAll("*").remove();
        
        // Altitude label (left)
        this.labelsGroup.append("text")
            .attr("transform", `translate(${-30}, ${this.innerHeight / 2}) rotate(-90)`)
            .attr("text-anchor", "middle")
            .style("fill", this.config.foreColor)
            .style("stroke", this.config.foreColor)
            .style("font-weight", "100")
            .text(`Altitude`);
        
        // Moon illumination label (right)
        this.labelsGroup.append("text")
            .attr("transform", `translate(${this.innerWidth + 35}, ${this.yMoonScale(50)}) rotate(90)`)
            .attr("text-anchor", "middle")
            .style("fill", this.config.foreColor)
            .style("stroke", this.config.moonDataColor)
            .style("font-weight", "100")
            .text("% Moon Illumination");
        
        // Time axis label (bottom)
        if (this.dsoData.length > 0) {
            const observingDt = this.dsoData[0].time;
            const year = observingDt.getFullYear();
            const timeStr = observingDt.toLocaleTimeString('en-US', { 
                hour: 'numeric', 
                minute: '2-digit',
                hour12: true 
            });
            
            this.labelsGroup.append("text")
                .attr("x", this.innerWidth / 2)
                .attr("y", this.innerHeight + 40)
                .attr("text-anchor", "middle")
                .style("fill", this.config.foreColor)
                .style("stroke", this.config.foreColor)
                .style("font-weight", "100")
                .text(`Year: ${year} DSO Elevation at ${timeStr} STANDARD time`);
        }
    }
    
    renderPaths() {
        this.pathsGroup.selectAll("*").remove();
        
        // DSO altitude path
        this.pathsGroup.append("path")
            .datum(this.dsoData)
            .attr("class", "dso-path")
            .attr("d", this.dsoPathLine)
            .attr("clip-path", "url(#cut-off-below-zero)")
            .style("fill", "none")
            .style("stroke", this.config.foreColor)
            .style("stroke-width", "2")
            .style("stroke-linecap", "round");
        
        // Moon illumination path
        this.pathsGroup.append("path")
            .datum(this.moonData)
            .attr("class", "moon-path")
            .attr("d", this.moonPathLine)
            .style("fill", "none")
            .style("stroke", this.config.moonDataColor)
            .style("stroke-width", "1")
            .style("stroke-linecap", "round");
    }
    
    renderDataPoints() {
        this.pointsGroup.selectAll("*").remove();
        
        // Filter to only show points above horizon
        const visiblePoints = this.dsoData.filter(d => d.alt > 0);
        
        // Create groups for each point
        const pointGroups = this.pointsGroup.selectAll("g.point-group")
            .data(visiblePoints)
            .join("g")
            .attr("class", "point-group")
            .attr("transform", d => `translate(${this.xScale(d.time)}, ${this.yScale(d.alt)})`);
        
        // Add circles
        pointGroups.append("circle")
            .attr("r", 2)
            .style("fill", "red");
        
        // Add labels
        pointGroups.append("text")
            .attr("dx", 5)
            .attr("dy", 0)
            .style("fill", this.config.foreColor)
            .style("stroke", this.config.foreColor)
            .style("stroke-width", "0.5")
            .style("font-size", "14px")
            .text(d => `${Math.round(d.alt)}° / ${Math.round(d.azi)}°`);
    }
    
    renderSafeAltLine() {
        this.safeAltGroup.selectAll("*").remove();
        
        this.safeAltGroup
            .attr("transform", `translate(0, ${this.yScale(this.config.safeAlt)})`)
            .append("line")
            .attr("x1", 0)
            .attr("x2", this.innerWidth)
            .style("stroke", "yellow")
            .style("stroke-width", "0.5");
    }
    
    renderNowLine() {
        const now = new Date();
        const scaledNow = this.xScale(now);
        
        this.nowLineGroup.selectAll("*").remove();
        
        const nowGroup = this.nowLineGroup
            .attr("transform", `translate(${scaledNow}, 0)`);
        
        // Vertical line
        nowGroup.append("line")
            .attr("y1", 10)
            .attr("y2", this.innerHeight)
            .style("stroke", "green")
            .style("stroke-width", "2");
        
        // Label
        nowGroup.append("text")
            .attr("transform", `translate(0, 100) rotate(-90)`)
            .attr("text-anchor", "middle")
            .attr("dy", "-3")
            .style("fill", "green")
            .style("stroke", "green")
            .style("font-size", "medium")
            .text(`Today   ${now.toLocaleDateString()}`);
    }
    
    updateTitle() {
        this.svg.select(".chart-title")
            .text(`9 PM Elevation / Moon Illumination for ${this.dsoName}`);
    }
    
    startTimeUpdater() {
        // Update "now" line every minute
        this.timeInterval = setInterval(() => {
            this.renderNowLine();
        }, 60 * 1000);
    }
    
    destroy() {
        if (this.timeInterval) {
            clearInterval(this.timeInterval);
        }
        this.container.selectAll("*").remove();
    }
}
