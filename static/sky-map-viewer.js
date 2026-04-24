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
        this.roll = 0;
        this.scaleValue = 200;

        // Drag-look state. Horizontal drag rotates around zenith; vertical drag moves zenith on center line.
        this.viewYawDeg = 0;
        this.zenithYTargetPx = null;

        // Drag tuning and clipping.
        this.xSign = options.xSign ?? -1;
        this.minPitchDeg = -85;
        this.maxPitchDeg = 85;
        this.zenithYMinPx = Math.round(this.height * 0.08);
        this.zenithYMaxPx = this.height / 2;
        
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
            .filter((event) => event.type === 'wheel')
            .on('zoom', (event) => this.zoomed(event));
        
        d3.select(this.canvas).call(zoomHandler);
        
        this.canvas.addEventListener('click', (event) => this.clickedCanvas(event));
    }
    
    calibrate(long, lat) {
        const xy1 = this.projection([long, lat]);
        const xy2 = this.projection([long + 5, lat + 5]);

        if (!xy1 || !xy2) {
            const fallbackDegPerPixel = (180 * Math.SQRT2) / (Math.PI * this.projection.scale());
            return [fallbackDegPerPixel * 3600, fallbackDegPerPixel];
        }
        
        const pixelsPerDegreeRA = (xy1[0] - xy2[0]) / 5.0;
        const pixelsPerDegreeDec = (xy1[1] - xy2[1]) / 5.0;

        if (Math.abs(pixelsPerDegreeRA) < 1e-9 || Math.abs(pixelsPerDegreeDec) < 1e-9) {
            const fallbackDegPerPixel = (180 * Math.SQRT2) / (Math.PI * this.projection.scale());
            return [fallbackDegPerPixel * 3600, fallbackDegPerPixel];
        }
        
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

    // drag by view (moving your head)
    draggedLook(event) {
        if (this.zenithYTargetPx === null) {
            this.syncLookStateToCurrentProjection();
        }

        const degPerPixel = this.getHorizontalDragDegreesPerPixel(event);
        const xSign = this.xSign ?? 1;
        this.viewYawDeg = wrapDeg(this.viewYawDeg + xSign * degPerPixel * event.dx);

        // Move zenith only along the image center line and never below the image midpoint.
        this.zenithYTargetPx = clamp(
            this.zenithYTargetPx + event.dy,
            this.zenithYMinPx,
            this.zenithYMaxPx
        );

        const [baseYaw, lstDeg] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);
        const yaw = wrapDeg(baseYaw + this.viewYawDeg);
        const pitch = this.solvePitchForZenithY(yaw, lstDeg, this.zenithYTargetPx);
        const roll = this.solveRollForZenithCenterline(yaw, pitch, lstDeg);

        this.projection.rotate([yaw, pitch, roll]);
        this.yaw = yaw;
        this.pitch = pitch;
        this.roll = roll;

        this.draw();
    }

    getHorizontalDragDegreesPerPixel(event) {
        const fallbackDegPerPixel = (180 * Math.SQRT2) / (Math.PI * this.projection.scale());

        if (!this.projection.invert) {
            return fallbackDegPerPixel;
        }

        const longLat = this.projection.invert([event.x, event.y]);
        if (!longLat) {
            return fallbackDegPerPixel;
        }

        const [arcsecPerPixelRA] = this.calibrate(longLat[0], longLat[1]);
        const degPerPixel = Math.abs(arcsecPerPixelRA / 3600);
        return Number.isFinite(degPerPixel) && degPerPixel > 0 ? degPerPixel : fallbackDegPerPixel;
    }

    getZenithLongLat(lstDeg) {
        return [this.ra2long(lstDeg / 15.0), this.observerLat];
    }

    createProjectionForRotation(yaw, pitch, roll = 0) {
        return d3.geoAzimuthalEqualArea()
            .scale(this.projection.scale())
            .translate(this.projection.translate())
            .center(this.projection.center())
            .rotate([yaw, pitch, roll])
            .reflectX(true);
    }

    projectPointAtRotation(longLat, yaw, pitch, roll = 0) {
        const tempProjection = this.createProjectionForRotation(yaw, pitch, roll);

        return tempProjection(longLat);
    }

    getZenithScreenAtRotation(yaw, pitch, roll, lstDeg) {
        return this.projectPointAtRotation(this.getZenithLongLat(lstDeg), yaw, pitch, roll);
    }

    getZenithCenteredYForPitch(yaw, pitch, lstDeg) {
        const zenithNoRoll = this.getZenithScreenAtRotation(yaw, pitch, 0, lstDeg);
        if (!zenithNoRoll) return null;

        const cx = this.width / 2;
        const cy = this.height / 2;
        const radialDistance = Math.hypot(zenithNoRoll[0] - cx, zenithNoRoll[1] - cy);

        return cy - radialDistance;
    }

    solvePitchForZenithY(yaw, lstDeg, targetY) {
        let lo = this.minPitchDeg;
        let hi = this.maxPitchDeg;

        let yLo = this.getZenithCenteredYForPitch(yaw, lo, lstDeg);
        let yHi = this.getZenithCenteredYForPitch(yaw, hi, lstDeg);

        if (yLo === null || yHi === null) {
            return clamp(this.pitch, this.minPitchDeg, this.maxPitchDeg);
        }

        // Normalize bracket ordering for the bisection step.
        if (yLo > yHi) {
            [lo, hi] = [hi, lo];
            [yLo, yHi] = [yHi, yLo];
        }

        const clampedTargetY = clamp(targetY, yLo, yHi);

        for (let i = 0; i < 16; i++) {
            const mid = (lo + hi) / 2;
            const yMid = this.getZenithCenteredYForPitch(yaw, mid, lstDeg);
            if (yMid === null) break;

            if (yMid < clampedTargetY) {
                lo = mid;
            } else {
                hi = mid;
            }
        }

        return clamp((lo + hi) / 2, this.minPitchDeg, this.maxPitchDeg);
    }

    solveRollForZenithCenterline(yaw, pitch, lstDeg) {
        const zenithNoRoll = this.getZenithScreenAtRotation(yaw, pitch, 0, lstDeg);
        if (!zenithNoRoll) return 0;

        const cx = this.width / 2;
        const cy = this.height / 2;
        const dx = zenithNoRoll[0] - cx;
        const dy = zenithNoRoll[1] - cy;

        if (Math.hypot(dx, dy) < 1e-6) {
            return this.roll || 0;
        }

        const base = Math.atan2(dx, dy) * 180 / Math.PI;
        const candidates = [base, base + 180, -base, -base + 180];

        let bestRoll = 0;
        let bestScore = Number.POSITIVE_INFINITY;

        candidates.forEach((candidate) => {
            const roll = wrapDeg(candidate);
            const z = this.getZenithScreenAtRotation(yaw, pitch, roll, lstDeg);
            if (!z) return;

            const xError = Math.abs(z[0] - cx);
            const belowMidlinePenalty = z[1] > cy ? 100000 + (z[1] - cy) * 1000 : 0;
            const yTargetPenalty = this.zenithYTargetPx === null ? 0 : Math.abs(z[1] - this.zenithYTargetPx);
            const score = xError + belowMidlinePenalty + (0.01 * yTargetPenalty);

            if (score < bestScore) {
                bestScore = score;
                bestRoll = roll;
            }
        });

        return bestRoll;
    }

    syncLookStateToCurrentProjection() {
        const [baseYaw, lstDeg] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);
        const currentRotation = this.projection.rotate();
        const currentYaw = currentRotation[0] ?? baseYaw;
        const currentPitch = currentRotation[1] ?? this.pitch;
        const currentRoll = (currentRotation[2] ?? this.roll) ?? 0;

        this.viewYawDeg = wrapDeg(currentYaw - baseYaw);

        const zenithScreen = this.getZenithScreenAtRotation(currentYaw, currentPitch, currentRoll, lstDeg);
        const fallbackY = zenithScreen ? zenithScreen[1] : (this.height / 2);
        this.zenithYTargetPx = clamp(fallbackY, this.zenithYMinPx, this.zenithYMaxPx);
    }

    getHorizonVerticalCenterAtRotation(yaw, pitch, roll) {
        const horizonCoords = this.geoHorizon?.features?.[0]?.geometry?.coordinates?.[0];
        if (!Array.isArray(horizonCoords) || horizonCoords.length === 0) {
            return null;
        }

        const tempProjection = this.createProjectionForRotation(yaw, pitch, roll);
        let minY = Number.POSITIVE_INFINITY;
        let maxY = Number.NEGATIVE_INFINITY;

        horizonCoords.forEach((longLat) => {
            const xy = tempProjection(longLat);
            if (!xy) return;
            minY = Math.min(minY, xy[1]);
            maxY = Math.max(maxY, xy[1]);
        });

        if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
            return null;
        }

        return (minY + maxY) / 2;
    }

    applyZoomScale(newScale) {
        this.scaleValue = clamp(newScale, 100, 3000);
        this.projection.scale(this.scaleValue);

        if (this.zenithYTargetPx === null) {
            this.syncLookStateToCurrentProjection();
        }

        const [baseYaw, lstDeg] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);
        const yaw = wrapDeg(baseYaw + this.viewYawDeg);

        // Keep the horizon centered vertically as zoom changes.
        const desiredHorizonCenterY = this.height / 2;
        let targetY = this.zenithYTargetPx ?? desiredHorizonCenterY;
        let pitch = this.pitch;
        let roll = this.roll;

        for (let i = 0; i < 6; i++) {
            pitch = this.solvePitchForZenithY(yaw, lstDeg, targetY);
            roll = this.solveRollForZenithCenterline(yaw, pitch, lstDeg);

            const horizonCenterY = this.getHorizonVerticalCenterAtRotation(yaw, pitch, roll);
            if (horizonCenterY === null) {
                break;
            }

            const errorY = desiredHorizonCenterY - horizonCenterY;
            if (Math.abs(errorY) < 0.5) {
                break;
            }

            targetY = clamp(targetY + 0.9 * errorY, this.zenithYMinPx, this.zenithYMaxPx);
        }

        this.zenithYTargetPx = targetY;
        pitch = this.solvePitchForZenithY(yaw, lstDeg, targetY);
        roll = this.solveRollForZenithCenterline(yaw, pitch, lstDeg);

        this.projection.rotate([yaw, pitch, roll]);
        this.yaw = yaw;
        this.pitch = pitch;
        this.roll = roll;

        this.draw();
    }
    
    dragStarted(event) {
        this.syncLookStateToCurrentProjection();
    }
    
    dragEnded(event) {
        // Optional: resume animation
    }
    
    zoomed(event) {
        this.applyZoomScale(event.transform.k);
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
        
        // Keep the drag-look orientation consistent while sidereal time advances.
        if (this.zenithYTargetPx === null) {
            this.syncLookStateToCurrentProjection();
        }
        const yaw = wrapDeg(newYaw + this.viewYawDeg);
        const pitch = this.solvePitchForZenithY(yaw, mst, this.zenithYTargetPx);
        const roll = this.solveRollForZenithCenterline(yaw, pitch, mst);

        this.projection.rotate([yaw, pitch, roll]);
        this.yaw = yaw;
        this.pitch = pitch;
        this.roll = roll;
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
        this.viewYawDeg = 0;
        
        const [yaw, mst] = this.getYawForTimeAndPlace(this.curVirtualTime, this.observerLong);
        const pitch = clamp(this.pitch, this.minPitchDeg, this.maxPitchDeg);
        
        this.currentMST = mst;
        this.geoHorizon = this.genLocalHorizon(this.curVirtualTime);
        this.geoLocalAltAz = this.genLocalAltAzMesh(this.curVirtualTime, this.observerLong, this.observerLat);
        
        this.projection.rotate([yaw, pitch, 0]);
        this.yaw = yaw;
        this.pitch = pitch;
        this.roll = 0;

        const zenithScreen = this.getZenithScreenAtRotation(yaw, pitch, 0, mst);
        const fallbackY = zenithScreen ? zenithScreen[1] : (this.height / 2);
        this.zenithYTargetPx = clamp(fallbackY, this.zenithYMinPx, this.zenithYMaxPx);
        
        this.draw();
    }
    
    adjustPitch(delta) {
        const newPitch = clamp(this.pitch + delta, this.minPitchDeg, this.maxPitchDeg);
        this.projection.rotate([this.yaw, newPitch, 0]);
        this.pitch = newPitch;
        this.roll = 0;
        this.syncLookStateToCurrentProjection();
        this.draw();
    }
    
    adjustYaw(delta) {
        this.yaw = wrapDeg(this.yaw + delta);
        this.projection.rotate([this.yaw, this.pitch, 0]);
        this.roll = 0;
        this.syncLookStateToCurrentProjection();
        this.draw();
    }
    
    adjustZoom(delta) {
        this.applyZoomScale(this.scaleValue + delta);
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