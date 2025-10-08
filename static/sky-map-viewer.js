/**
 * Interactive Sky Map Viewer
 * Converts Svelte sky map to vanilla JavaScript for FastHTML
 */

// Import astronomy utility functions
import { 
    coord_to_horizon, 
    horiz_to_equitorial, 
    mean_sidereal_time, 
    degree2HMS,
    // ae_get_constellation_name 
} from '/static/astronomy-utils.js';

// helpers from GPT

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function wrapDeg(d) { return ((d + 180) % 360 + 360) % 360 - 180; }

function cameraBasis(yawDeg, pitchDeg) {
  const y = (Math.PI/180)*yawDeg, p = (Math.PI/180)*pitchDeg;
  const cy = Math.cos(y), sy = Math.sin(y);
  const cp = Math.cos(p), sp = Math.sin(p);
  const forward = norm([ cy*cp, sp, -sy*cp ]);
  const worldUp = [0, 1, 0];
  const right   = norm(cross(worldUp, forward));
  const up0     = norm(cross(forward, right));
  return {forward, right, up0};
}

function upRefVector(mode, { lstDeg, observerLatDeg, forward, right, up0 }) {
  if (mode === 'celestial-north') return [0,0,1]; // NCP
  if (mode === 'compass-up') {
    const ncp = [0,0,1];
    // project NCP onto image plane (⊥ forward)
    const proj = [
      ncp[0] - dot(ncp, forward)*forward[0],
      ncp[1] - dot(ncp, forward)*forward[1],
      ncp[2] - dot(ncp, forward)*forward[2],
    ];
    return norm(proj);
  }
  // default 'zenith'
  return vecFromRaDec(lstDeg, observerLatDeg); // local zenith in equatorial frame
}

function rollToAlignUp(yawDeg, pitchDeg, upRefWorld, basis, rollSign = 1) {
  const u = dot(upRefWorld, basis.up0);
  const r = dot(upRefWorld, basis.right);
  // Guard singularity when upRef ~ forward (very near center)
  const eps = 1e-6;
  if (Math.abs(u) < eps && Math.abs(r) < eps) return 0;
  return rollSign * Math.atan2(r, u) * 180/Math.PI;
}

// vector utils
function dot(a,b){ return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
function cross(a,b){ return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]; }
function norm(v){ const n=Math.hypot(v[0],v[1],v[2])||1; return [v[0]/n,v[1]/n,v[2]/n]; }
function vecFromRaDec(raDeg, decDeg) {
  const a = Math.PI/180*raDeg, d = Math.PI/180*decDeg;
  const cd = Math.cos(d), sd = Math.sin(d);
  const ca = Math.cos(a), sa = Math.sin(a);
  return [cd*ca, cd*sa, sd];
}


class SkyMapViewer {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        
        // Configuration
        this.width = options.width || 1400;
        this.height = options.height || 900;
        this.canvas.width = this.width;
        this.canvas.height = this.height;
        
        // Observer location
        this.observerLat = options.observerLat || 0;
        this.observerLong = options.observerLong || 0;
        
        // Projection state
        this.yaw = 0;
        this.pitch = 0;
        this.scaleValue = 200;

        // upMode added for 'drag view' mode (instead of drag time)
        this.upMode = options.upMode || 'zenith'; // 'zenith', 'celestial-north', 'compass-up'

        // added for 'drag view' mode (instead of drag time)
        // Camera/view offsets (degrees). These are RELATIVE to your time-based baseYaw.
        this.viewYawDeg   = this.viewYawDeg   ?? 0;
        this.viewPitchDeg = this.viewPitchDeg ?? 0;

        // Optional: baseline pitch for your default orientation (often 0).
        this.basePitchDeg = this.basePitchDeg ?? 0;

        // If you support "west-left/right" flipping, set this to +1 or -1.
        this.xSign = this.xSign ?? -1;
        
        // Time management
        this.curVirtualTime = new Date();
        this.mSecAhead = 0;
        this.timerRunning = false;
        this.heartbeatInterval = 1000; // ms
        this.lastUpdateTime = new Date();
        
        // Data storage
        this.stars6 = null;
        this.constellationLines = null;
        this.constellationBounds = null;
        this.messier = null;
        this.asterisms = null;
        this.starnames = {};
        
        // Highlighted state
        this.highlightedConstellation = null;
        this.nearestDSO = null;
        this.nearestStarId = null;
        this.constellationName = "";
        
        // Quadtree indexes
        this.invertedIndexForDSOs = null;
        this.invertedIndexForStars = null;
        
        // Setup projection
        this.initProjection();
        
        // Setup scales
        this.initScales();
        
        // Coordinate conversion meshes
        this.geoRaDecMesh = null;
        this.geoHorizon = null;
        this.geoLocalAltAz = null;
        this.currentMST = 0;
        
        // Bind event handlers
        this.setupInteractions();
    }
    
    initProjection() {
        const margin = 20;
        
        this.projection = d3.geoAzimuthalEqualArea()
            .fitExtent([[margin, margin], [this.width - margin, this.height - margin]], 
                       {type: "Sphere"})
            .center([0, 0])
            .scale(this.scaleValue)
            .rotate([this.yaw, this.pitch, 0])
            .reflectX(true); // Match SkySafari orientation
        
        this.updatePathGenerators();
    }
    
    updatePathGenerators() {
        this.starPath = d3.geoPath()
            .projection(this.projection)
            .context(this.ctx);
        
        this.regularPath = d3.geoPath()
            .projection(this.projection)
            .context(this.ctx);
    }
    
    initScales() {
        this.magnitudeScale = d3.scaleLinear()
            .domain([6, 0])
            .range([0.5, 2]); // keep stars smaller
    }
    
    // ============ Coordinate conversion utilities ============
    
    ra2long(ra) {
        // RA in decimal hours to longitude
        return ra > 12 ? (ra - 24) * 15 : ra * 15;
    }
    
    long2ra(long) {
        // Longitude to RA in decimal hours
        return long < 0 ? (360 + long) / 15 : long / 15;
    }
    
    dec2lat(dec) {
        return dec; // Already in degrees
    }
    
    yaw2MeridianRA(yaw) {
        let yaw2 = -yaw % 360;
        yaw2 = (yaw2 < -180) ? yaw2 + 360 : yaw2;
        yaw2 = (yaw2 > 180) ? yaw2 - 360 : yaw2;
        return this.long2ra(yaw2);
    }
    
    meridianRa2yaw(ra) {
        const long = this.ra2long(ra);
        return -long;
    }
    
    // ============ Mesh generation (from my original code) ============
    
    genGeoRaDecMesh() {
        const skipHours = 1;
        const skipDec = 10;
        const features = [];
        
        const newFeature = (name, loc, orientation, coords) => ({
            type: "Feature",
            id: "",
            properties: { n: name, loc, orientation },
            geometry: { type: "MultiLineString", coordinates: coords }
        });
        
        // Lines of constant RA
        for (let ra = 0; ra < 24; ra += skipHours) {
            for (let dec = -90; dec <= 90; dec += skipDec) {
                const startPt = dec;
                const midPt = dec + skipDec / 2.0;
                const endPt = dec + skipDec;
                const coords = [
                    [[this.ra2long(ra), this.dec2lat(startPt)], [this.ra2long(ra), this.dec2lat(midPt)]],
                    [[this.ra2long(ra), this.dec2lat(midPt)], [this.ra2long(ra), this.dec2lat(endPt)]]
                ];
                features.push(newFeature(`${Math.round(ra)}h`, [this.ra2long(ra), this.dec2lat(midPt)], -90, coords));
            }
        }
        
        // Lines of constant DEC
        for (let dec = -90; dec <= 90; dec += skipDec) {
            for (let ra = 0; ra <= 24; ra += skipHours) {
                const startPt = ra;
                const midPt = ra + skipHours / 2.0;
                const endPt = ra + skipHours;
                const coords = [
                    [[this.ra2long(startPt), this.dec2lat(dec)], [this.ra2long(midPt), this.dec2lat(dec)]],
                    [[this.ra2long(midPt), this.dec2lat(dec)], [this.ra2long(endPt), this.dec2lat(dec)]]
                ];
                features.push(newFeature(`${Math.round(dec)}°`, [this.ra2long(midPt), this.dec2lat(dec + 0.05)], 0, coords));
            }
        }
        
        return { type: "FeatureCollection", features };
    }
    
    genLocalHorizon(dt) {
        const skipAzi = 10; // degrees
        const alt = 0; // horizon
        const poly = [];
        
        for (let azi = 0; azi < 360; azi += skipAzi) {
            // Convert local alt/azi to RA/DEC
            const [ra, dec] = horiz_to_equitorial(dt, alt, azi, this.observerLong, this.observerLat);
            const raHr = ra / 15.0;
            const long = this.ra2long(raHr);
            const lat = this.dec2lat(dec);
            poly.push([long, lat]);
        }
        
        return {
            type: "FeatureCollection",
            features: [{
                type: "Feature",
                id: "",
                properties: { n: "local horizon", loc: [0, 0] },
                geometry: { type: "Polygon", coordinates: [poly] }
            }]
        };
    }
    
    genLocalAltAzMesh(now, localLong, localLat) {
        const features = [];
        
        const newLineFeature = (name, loc, orientation, coords) => ({
            type: "Feature",
            id: "Azimuth",
            properties: { n: name, loc, orientation },
            geometry: { type: "LineString", coordinates: coords }
        });
        
        const newPolygonFeature = (name, loc, orientation, coords) => ({
            type: "Feature",
            id: "Altitude",
            properties: { n: name, loc, orientation },
            geometry: { type: "Polygon", coordinates: [coords] }
        });
        
        const newCardinalPointFeature = (name, loc, orientation, coords) => ({
            type: "Feature",
            id: "CardinalPoint",
            properties: { n: name, loc, orientation },
            geometry: { type: "Point", coordinates: [coords] }
        });
        
        const localAltAzi2LongLat = (alt, azi) => {
            const [ra, dec] = horiz_to_equitorial(now, alt, azi, localLong, localLat);
            const long = this.ra2long(ra / 15.0);
            const lat = this.dec2lat(dec);
            return [long, lat];
        };
        
        const skipAzi = 20;
        const alt = 0;
        
        // Lines of constant azimuth
        for (let azi = 0; azi < 360; azi += skipAzi) {
            const [topLong, topLat] = localAltAzi2LongLat(80, azi);
            const [horizLong, horizLat] = localAltAzi2LongLat(alt, azi);
            const coords = [[topLong, topLat], [horizLong, horizLat]];
            features.push(newLineFeature(`${Math.round(azi)}°`, [horizLong, horizLat], 0, coords));
        }
        
        // Lines of constant altitude
        const skipAlt = 10;
        for (let alt = skipAlt; alt < 90; alt += skipAlt) {
            const polygon = [];
            for (let azi = 0; azi < 360; azi += 10) {
                const [long, lat] = localAltAzi2LongLat(alt, azi);
                polygon.push([long, lat]);
            }
            polygon.push(polygon[0]); // Close polygon
            features.push(newPolygonFeature(`${Math.round(alt)}°`, [0, 0], 0, polygon));
        }
        
        // Cardinal points
        features.push(newCardinalPointFeature("N", [0, 0], 0, localAltAzi2LongLat(0, 0)));
        features.push(newCardinalPointFeature("S", [0, 0], 0, localAltAzi2LongLat(0, 180)));
        features.push(newCardinalPointFeature("E", [0, 0], 0, localAltAzi2LongLat(0, 90)));
        features.push(newCardinalPointFeature("W", [0, 0], 0, localAltAzi2LongLat(0, 270)));
        features.push(newCardinalPointFeature("Z", [0, 0], 0, localAltAzi2LongLat(90, 0)));
        
        return { type: "FeatureCollection", features };
    }
    
    // ============ Quadtree for click detection ============
    
    buildInvertedIndexForDSOs(features) {
        const qTree = d3.quadtree()
            .x(d => d.geometry.centroid[0])
            .y(d => d.geometry.centroid[1]);
        
        features.forEach(feature => {
            feature.geometry.centroid = d3.geoCentroid(feature);
            qTree.add(feature);
        });
        
        return qTree;
    }
    
    buildInvertedIndexForStars(features) {
        const qTree = d3.quadtree()
            .x(d => d.geometry.coordinates[0])
            .y(d => d.geometry.coordinates[1]);
        
        features.forEach(feature => {
            qTree.add(feature);
        });
        
        return qTree;
    }
    
    // ============ Interaction handlers ============
    
    setupInteractions() {
        const dragHandler = d3.drag()
            .on('drag', (event) => this.draggedLook(event)) // this.draggedLook(event)) // this.dragged(event))
            .on('start', (event) => this.dragStarted(event))
            .on('end', (event) => this.dragEnded(event));
        
        d3.select(this.canvas).call(dragHandler);
        
        const zoomHandler = d3.zoom()
            .scaleExtent([100, 3000])
            .on('zoom', (event) => this.zoomed(event));
        
        d3.select(this.canvas).call(zoomHandler);
        
        this.canvas.addEventListener('click', (event) => this.clickedCanvas(event));
    }
    
    calibrate(long, lat) {
        const xy1 = this.projection([long, lat]);
        const xy2 = this.projection([long + 5, lat + 5]);
        
        const pixelsPerDegreeRA = (xy1[0] - xy2[0]) / 5.0;
        const pixelsPerDegreeDec = (xy1[1] - xy2[1]) / 5.0;
        
        const arcsecPerPixelRA = 3600 / pixelsPerDegreeRA;
        const degreesPerPixelDec = 1 / pixelsPerDegreeDec;
        
        return [arcsecPerPixelRA, degreesPerPixelDec];
    }
    
    // drag by time (original approach, moving RA instead of viewer's head)
    dragged(event) {
        const hereLongLat = this.projection.invert ? this.projection.invert([event.x, event.y]) : [0, 0];
        const [arcsecPerPixelRA, degreesPerPixelDec] = this.calibrate(hereLongLat[0], hereLongLat[1]);
        const mArcSecPerPix = arcsecPerPixelRA * 1000;
        const msecTimePerPix = mArcSecPerPix / 15.0; // 15 degrees per RA hour
        
        // Y-axis motion (pitch/declination)
        const startPitch = this.projection.rotate()[1];
        const newPitch = startPitch - degreesPerPixelDec * event.dy;
        
        // X-axis motion (time/RA)
        this.mSecAhead += msecTimePerPix * event.dx;
        this.curVirtualTime = new Date(Date.now() + this.mSecAhead);
        
        const [newYaw, mst] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);

        // Update local coordinate meshes
        this.geoHorizon = this.genLocalHorizon(this.curVirtualTime);
        this.geoLocalAltAz = this.genLocalAltAzMesh(this.curVirtualTime, this.observerLong, this.observerLat);

        this.projection.rotate([newYaw, newPitch, 0]);
        this.yaw = newYaw;
        this.pitch = newPitch;
        
        this.draw();
    }

    // drag by view (moving your head) (new approach)
    draggedLook(event) {
        // Initialize view offsets if not set - start from current rotation
        if (this.viewYawDeg === undefined) {
            const currentRotation = this.projection.rotate();
            this.viewYawDeg = 0;
            this.viewPitchDeg = 0;
            this.basePitchDeg = currentRotation[1] || 0; // Start from current pitch
            
            // Calculate initial roll from current state to avoid jump
            const currentYaw = currentRotation[0] || 0;
            const currentPitch = currentRotation[1] || 0;
            this.lastRoll = this.calculateZenithUpRoll(currentYaw, currentPitch);
        }

        // Use same coordinate system as working dragged() function
        const hereLongLat = this.projection.invert ? this.projection.invert([event.x, event.y]) : [0, 0];
        const [arcsecPerPixelRA, degreesPerPixelDec] = this.calibrate(hereLongLat[0], hereLongLat[1]);
        
        // Apply drag movements directly to view offsets (like in dragged())
        const xSign = (this.xSign ?? 1);
        this.viewYawDeg   += xSign * (arcsecPerPixelRA / 3600) * event.dx;
        this.viewPitchDeg -= degreesPerPixelDec * event.dy;
        
        // Clamp pitch to avoid gimbal lock
        this.viewPitchDeg = clamp(this.viewPitchDeg, -89.9, 89.9);

        // 3) Get base yaw from time + longitude (do NOT change time in look mode)
        const [baseYaw, lstDeg] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);

        // Apply offsets to base values
        const yaw   = baseYaw + this.viewYawDeg;
        const pitch = clamp(this.basePitchDeg + this.viewPitchDeg, -89.9, 89.9);

        // Simple "zenith up" roll calculation
        const roll = this.calculateZenithUpRoll(yaw, pitch, lstDeg);

        // Apply directly to projection like dragged() does
        this.projection.rotate([yaw, pitch, roll]);
        this.yaw = yaw; this.pitch = pitch; this.roll = roll; 

        this.draw();
    }

    // Simple roll calculation to keep zenith pointing "up" in the viewport
    // still not close to good enough.
    calculateZenithUpRoll(yaw, pitch, lstDeg) {
        // For very small movements, don't recalculate roll to avoid jerkiness
        const currentRotation = this.projection.rotate();
        if (this.lastRoll !== undefined) {
            const yawDiff = Math.abs(currentRotation[0] - (this.lastYaw || 0));
            const pitchDiff = Math.abs(currentRotation[1] - (this.lastPitch || 0));
            
            // If movement is very small, keep previous roll
            if (yawDiff < 1 && pitchDiff < 1) {
                return this.lastRoll;
            }
        }
        
        // Zenith in RA/Dec coordinates is at (LST, observer_latitude)
        const zenithRA = lstDeg; // LST in degrees
        const zenithDec = this.observerLat; // Observer latitude
        
        // Convert zenith RA/Dec to projection coordinates
        const zenithLong = this.ra2long(zenithRA / 15.0); // Convert to hours first
        const zenithLat = zenithDec;
        
        // Create a temporary projection with no roll to calculate zenith position
        const tempProjection = d3.geoAzimuthalEqualArea()
            .scale(this.projection.scale())
            .center(this.projection.center())
            .rotate([yaw, pitch, 0]) // No roll for calculation
            .reflectX(true);
        
        // Project zenith point to screen coordinates using temp projection
        const zenithScreen = tempProjection([zenithLong, zenithLat]);
        
        if (!zenithScreen) {
            // If zenith not visible, smoothly transition to no roll
            const targetRoll = 0;
            const smoothingFactor = 0.1;
            const newRoll = this.lastRoll ? this.lastRoll * (1 - smoothingFactor) + targetRoll * smoothingFactor : targetRoll;
            this.lastRoll = newRoll;
            this.lastYaw = yaw;
            this.lastPitch = pitch;
            return newRoll;
        }
        
        // Calculate vector from screen center to zenith
        const centerX = this.width / 2;
        const centerY = this.height / 2;
        const dx = zenithScreen[0] - centerX;
        const dy = zenithScreen[1] - centerY;
        
        // Calculate angle to make zenith point "up" (negative Y direction)
        const targetAngle = -Math.PI / 2; // Pointing up
        const currentAngle = Math.atan2(dy, dx);
        
        // Calculate the roll needed to align zenith with "up"
        let roll = (targetAngle - currentAngle) * 180 / Math.PI;
        
        // Normalize roll to [-180, 180] range
        while (roll > 180) roll -= 360;
        while (roll < -180) roll += 360;
        
        // Smooth the roll transition to avoid jerkiness
        if (this.lastRoll !== undefined) {
            const rollDiff = roll - this.lastRoll;
            // Handle wraparound
            if (rollDiff > 180) roll -= 360;
            if (rollDiff < -180) roll += 360;
            
            // Apply smoothing
            const smoothingFactor = 0.1; // Adjust this value (0.1 = very smooth, 0.9 = very responsive)
            roll = this.lastRoll * (1 - smoothingFactor) + roll * smoothingFactor;
        }
        
        // Store for next calculation
        this.lastRoll = roll;
        this.lastYaw = yaw;
        this.lastPitch = pitch;
        
        return roll;
    }
    
    dragStarted(event) {
        // Optional: pause animation during drag
    }
    
    dragEnded(event) {
        // Optional: resume animation
    }
    
    zoomed(event) {
        this.scaleValue = event.transform.k;
        this.projection.scale(this.scaleValue);
        this.draw();
    }
    
    clickedCanvas(event) {
        const rect = this.canvas.getBoundingClientRect();
        const point = [event.clientX - rect.left, event.clientY - rect.top];
        
        if (!this.projection.invert) return;
        
        const longLat = this.projection.invert(point);
        if (!longLat) return;
        
        console.log("Clicked at longLat:", longLat);
        
        // Find nearest DSO
        this.nearestDSO = this.invertedIndexForDSOs ? 
            this.invertedIndexForDSOs.find(longLat[0], longLat[1], 2) : null;
        
        // Find nearest star
        const nearestStar = this.invertedIndexForStars ? 
            this.invertedIndexForStars.find(longLat[0], longLat[1], 1) : null;
        this.nearestStarId = nearestStar ? nearestStar.id : null;
        
        // Get constellation name (if function available)
        if (typeof ae_get_constellation_name === 'function') {
            const [constName, constSymbol] = ae_get_constellation_name(longLat[0], longLat[1]);
            this.constellationName = constName;
            
            // Find and highlight constellation
            let found = null;
            if (this.constellationBounds) {
                for (let feature of this.constellationBounds.features) {
                    if (feature.id === constSymbol) {
                        found = feature;
                        break;
                    }
                }
            }
            
            // Toggle highlighting
            if (!this.nearestDSO && !this.nearestStarId && this.highlightedConstellation === found) {
                this.highlightedConstellation = null;
            } else {
                this.highlightedConstellation = found;
            }
        }
        
        this.draw();
        
        // Trigger custom event with click data
        this.canvas.dispatchEvent(new CustomEvent('skyObjectClicked', {
            detail: {
                dso: this.nearestDSO,
                starId: this.nearestStarId,
                starName: this.starnames[this.nearestStarId],
                constellation: this.constellationName
            }
        }));
    }
    
    // ============ Time and coordinate calculations ============
    
    getYawForTimeAndPlace(date, longitude) {
        // Calculate mean sidereal time (in DECIMAL DEGREES)
        const mst = mean_sidereal_time(date, longitude);
        const ra = mst / 15.0; // Convert to hours
        const yaw = this.meridianRa2yaw(ra);
        return [yaw, mst];
    }
    
    // ============ Drawing functions ============
    
    draw() {
        if (!this.ctx) return;
        
        // Clear canvas
        this.ctx.beginPath();
        this.ctx.fillStyle = "black";
        this.ctx.rect(0, 0, this.width, this.height);
        this.ctx.fill();
        
        // Draw stars
        if (this.stars6) {
            this.drawStars();
        }
        
        // Draw constellation lines
        if (this.constellationLines) {
            this.drawConstellationLines();
        }
        
        // Draw DSOs
        if (this.messier) {
            this.drawDSOs();
        }
        
        // Draw RA/DEC grid
        if (this.geoRaDecMesh) {
            this.drawRADecGrid();
        }
        
        // Draw local horizon
        if (this.geoHorizon) {
            this.drawHorizon();
        }
        
        // Draw local alt/az mesh
        if (this.geoLocalAltAz) {
            this.drawLocalAltAz();
        }
        
        // Draw highlighted constellation
        if (this.highlightedConstellation) {
            this.drawHighlightedConstellation();
        }
    }
    
    drawStars() {
        this.ctx.beginPath();
        this.ctx.fillStyle = "pink";
        this.ctx.strokeStyle = "pink";
        
        this.stars6.features.forEach(star => {
            if (star && star.properties && star.properties.mag < 6.0) {
                const radius = this.magnitudeScale(star.properties.mag);
                this.starPath.pointRadius(radius);
                this.starPath(star);
            }
        });
        
        this.ctx.fill();
    }
    
    drawConstellationLines() {
        this.ctx.beginPath();
        this.ctx.strokeStyle = "rgb(255, 255, 255)";
        this.ctx.lineWidth = 1;
        this.ctx.setLineDash([]);
        this.regularPath(this.constellationLines);
        this.ctx.stroke();
    }
    
    drawDSOs() {
        this.messier.features.forEach(dso => {
            this.drawDSO(dso, this.projection.scale());
        });
    }
    
    drawDSO(feature, curZoom) {
        const xy = this.projection(feature.geometry.coordinates);
        if (!xy) return;
        
        const rawSize = feature.properties.dim || "10";
        const sizes = rawSize.split("x");
        const diagSize = Math.sqrt(
            Math.pow(parseInt(sizes[0]), 2) + 
            Math.pow(sizes.length > 1 ? parseInt(sizes[1]) : 1, 2)
        );
        
        const [asPerPixLocal, _] = this.calibrate(
            feature.geometry.coordinates[0], 
            feature.geometry.coordinates[1]
        );
        
        let scaleSize = (diagSize * 60.0) / asPerPixLocal; // arcmin to arcsec, then pixels
        let radius = Math.max(5, Math.min(20, scaleSize));
        
        this.ctx.beginPath();
        this.ctx.fillStyle = "rgba(200, 100, 100, 0.3)";
        this.ctx.strokeStyle = "rgba(200, 100, 100, 1)";
        this.ctx.arc(xy[0], xy[1], radius, 0, 2 * Math.PI);
        this.ctx.stroke();
        this.ctx.fill();
        
        // Draw label if zoomed in
        const minZoom = 400;
        if (curZoom > minZoom) {
            this.ctx.beginPath();
            this.ctx.font = "14px serif";
            this.ctx.textAlign = "left";
            this.ctx.textBaseline = "middle";
            this.ctx.strokeStyle = "rgb(128, 128, 128)";
            this.ctx.lineWidth = 0.75;
            this.ctx.strokeText(feature.id || "", xy[0] + radius + 3, xy[1]);
            this.ctx.stroke();
        }
    }
    
    drawRADecGrid() {
        this.ctx.beginPath();
        this.ctx.strokeStyle = "green";
        this.ctx.lineWidth = 1;
        this.ctx.setLineDash([]);
        this.regularPath(this.geoRaDecMesh);
        this.ctx.stroke();
        
        // Draw labels if zoomed in
        if (this.projection.scale() > 500) {
            this.ctx.strokeStyle = "green";
            this.ctx.textAlign = "center";
            this.ctx.textBaseline = "bottom";
            this.ctx.font = "14px sans-serif";
            
            let counter = 0;
            this.geoRaDecMesh.features.forEach(element => {
                if (counter++ % 2) {
                    const xy = this.projection(element.properties.loc);
                    if (!xy) return;
                    
                    this.ctx.beginPath();
                    if (element.properties.orientation !== 0) {
                        this.ctx.save();
                        this.ctx.translate(xy[0], xy[1]);
                        this.ctx.rotate(element.properties.orientation * Math.PI / 180);
                        this.ctx.strokeText(element.properties.n, 0, 0);
                        this.ctx.restore();
                    } else {
                        this.ctx.strokeText(element.properties.n, xy[0], xy[1]);
                    }
                }
            });
        }
    }
    
    drawHorizon() {
        this.ctx.beginPath();
        this.ctx.strokeStyle = "red";
        this.ctx.lineWidth = 2;
        this.ctx.setLineDash([]);
        this.regularPath(this.geoHorizon);
        this.ctx.stroke();
    }
    
    drawLocalAltAz() {
        this.ctx.beginPath();
        this.ctx.strokeStyle = "rgb(180, 180, 0)";
        this.ctx.lineWidth = 1;
        this.ctx.setLineDash([]);
        this.regularPath(this.geoLocalAltAz);
        this.ctx.stroke();
        
        // Draw cardinal points
        this.ctx.beginPath();
        this.ctx.textAlign = "center";
        this.ctx.textBaseline = "middle";
        this.ctx.font = "24px serif";
        this.ctx.strokeStyle = "red";
        this.ctx.fillStyle = "red";
        
        this.geoLocalAltAz.features.forEach(d => {
            if (d.id === "CardinalPoint") {
                const letter = d.properties.n;
                const xy = this.projection(d.geometry.coordinates[0]);
                if (!xy) return;
                this.ctx.strokeText(letter, xy[0], xy[1]);
                this.ctx.fillText(letter, xy[0], xy[1]);
            }
        });
        
        this.ctx.stroke();
        this.ctx.fill();
    }
    
    drawHighlightedConstellation() {
        this.ctx.beginPath();
        this.ctx.strokeStyle = "rgba(255, 150, 250, 1)";
        this.ctx.lineWidth = 2;
        this.ctx.setLineDash([1, 1]);
        this.regularPath(this.highlightedConstellation);
        this.ctx.stroke();
        this.ctx.setLineDash([]);
    }
    
    // ============ Data loading ============
    
    async loadData(apiEndpoint) {
        try {
            const response = await fetch(apiEndpoint);
            const data = await response.json();
            
            this.stars6 = data.stars6;
            this.constellationLines = data.constellationLines;
            this.constellationBounds = data.constellationBounds;
            this.messier = data.messier;
            this.asterisms = data.asterisms;
            this.starnames = data.starnames || {};
            this.observerLat = data.observerLat;
            this.observerLong = data.observerLong;
            
            // Update magnitude scale domain
            if (this.stars6) {
                const magExtent = d3.extent(this.stars6.features, d => d.properties.mag);
                this.magnitudeScale.domain(magExtent);
            }
            
            // Build quadtree indexes
            if (this.messier) {
                this.invertedIndexForDSOs = this.buildInvertedIndexForDSOs(this.messier.features);
            }
            if (this.stars6) {
                this.invertedIndexForStars = this.buildInvertedIndexForStars(this.stars6.features);
            }
            
            // Generate coordinate meshes
            this.geoRaDecMesh = this.genGeoRaDecMesh();
            this.geoHorizon = this.genLocalHorizon(this.curVirtualTime);
            this.geoLocalAltAz = this.genLocalAltAzMesh(this.curVirtualTime, this.observerLong, this.observerLat);
            
            // Initial orientation to here and now
            this.gotoHereAndNow();
            
            // Start animation timer
            this.startTimer();
            
        } catch (error) {
            console.error("Error loading sky map data:", error);
        }
    }
    
    // ============ Time animation (heartbeat) ============
    
    startTimer() {
        if (this.timerRunning) return;
        this.timerRunning = true;
        //t this.processHeartbeat();
    }
    
    stopTimer() {
        this.timerRunning = false;
    }
    
    processHeartbeat() {
        if (!this.timerRunning) return;
        
        // Update virtual time
        this.curVirtualTime = new Date(Date.now() + this.mSecAhead);
        
        // Calculate new yaw for current time
        const [newYaw, mst] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);
        
        // Update local coordinate meshes
        this.geoHorizon = this.genLocalHorizon(this.curVirtualTime);
        this.geoLocalAltAz = this.genLocalAltAzMesh(this.curVirtualTime, this.observerLong, this.observerLat);
        
        // Update projection
        this.projection.rotate([newYaw, this.pitch, 0]);
        this.yaw = newYaw;
        this.currentMST = mst;
        
        // Redraw
        this.draw();
        
        // Schedule next heartbeat
        setTimeout(() => this.processHeartbeat(), this.heartbeatInterval);
    }
    
    // ============ Navigation controls ============
    
    gotoHereAndNow() {
        this.curVirtualTime = new Date();
        this.mSecAhead = 0;
        
        const [yaw, mst] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);
        
        this.currentMST = mst;
        this.geoHorizon = this.genLocalHorizon(this.curVirtualTime);
        this.geoLocalAltAz = this.genLocalAltAzMesh(this.curVirtualTime, this.observerLong, this.observerLat);
        
        this.projection.rotate([yaw, this.pitch, 0]);
        this.yaw = yaw;
        
        this.draw();
    }
    
    adjustPitch(delta) {
        const newPitch = Math.max(-90, Math.min(90, this.pitch + delta));
        this.projection.rotate([this.yaw, newPitch, 0]);
        this.pitch = newPitch;
        this.draw();
    }
    
    adjustYaw(delta) {
        this.yaw += delta;
        this.projection.rotate([this.yaw, this.pitch, 0]);
        this.draw();
    }
    
    adjustZoom(delta) {
        this.scaleValue = Math.max(100, Math.min(3000, this.scaleValue + delta));
        this.projection.scale(this.scaleValue);
        this.draw();
    }
    
    saveAsImage(filename = 'sky-map.png') {
        if (!this.canvas) return;
        
        const dataUrl = this.canvas.toDataURL("image/png", 1.0);
        const link = document.createElement('a');
        link.download = filename;
        link.href = dataUrl;
        link.click();
    }
    
    // ============ Utility methods ============
    
    getStatus() {
        return {
            yaw: this.yaw,
            pitch: this.pitch,
            scale: this.scaleValue,
            meridianRA: this.yaw2MeridianRA(this.yaw),
            currentMST: this.currentMST,
            virtualTime: this.curVirtualTime,
            mSecAhead: this.mSecAhead
        };
    }
    
    destroy() {
        this.stopTimer();
        // Clean up event listeners if needed
    }
}

// Export for ES modules
export { SkyMapViewer };