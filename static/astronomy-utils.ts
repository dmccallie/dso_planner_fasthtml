// collection of various conversion routines for astronomy, etc
// convert ts to js using:
// tsc ./static/astronomy-utils.ts  --skipLibCheck  --target es2020 --module esnext

//parse a traditional RA string into a real number of degrees 
//fixme - requires all three HMS values - allow for missing seconds?

export function parseRA(ra_string:string) : number {
    const ra_hms_style = /([0-9]+)\s*[H]\s*([0-9]+)\s*[M]\s*([0-9.]+)[S]/i; //requires HMS or hms
    const ra_colon_style = /([0-9]+)\s*[:]\s*([0-9]+)\s*[:]\s*([0-9.]+)/;  //requires first two colons
    const ra_spaces_style = /([0-9]+)\s*([0-9]+)\s*([0-9.]+)/;  //SIMBAD's format

    //console.log("parse RA with: ", ra_string, " as arg")
    let results = ra_string.match(ra_hms_style); //try HMS style
    if (results === null) {
        results = ra_string.match(ra_colon_style); //try colon style
    }
    if (results === null) {
    results = ra_string.match(ra_spaces_style); //try SIMBAD style
    }
    if (results === null) {
        console.log("Cannot parse RA from: ", ra_string)
        return -1; //FIXME
    } 

    const hours = parseInt(results[1]) //first group
    //fixme add checks for negative or floating point hours and mins
    const mins = parseInt(results[2]);
    const secs = parseFloat(results[3]);

    const answer = ra2real(hours, mins, secs);
    return answer;
}


//parse traditional DEC (lat and long too) string into a real number of degrees 
//fixme - requires all three HMS - allow for missing seconds?
export function parseDEC(dec_string:string): number {
    const dec_colon_style = /([\+\-0-9]+)\s*[:]\s*([0-9]+)\s*[:]\s*([0-9.]+)/; //spaces OK
    const dec_degree_style = /([\+\-0-9]+)[\u00b0]\s*([0-9]+)\'\s*([0-9.]+)\"/;  //no spaces here
    const dec_spaces_style = /([\+\-0-9]+)\s*([0-9]+)\s*([0-9.]+)/;  //SIMBAD format

    //console.log(dec_string)
    let results = dec_string.match(dec_colon_style); //try colon style
    if (results === null) {
        results = dec_string.match(dec_degree_style); //try degree style
    }
    if (results === null) {
        results = dec_string.match(dec_spaces_style); //try SIMBAD style
    }
    if (results === null) {
        console.log("Cannot parse DEC from: ", dec_string);
        return(-1) //FIXME
    } 
    const degs = parseInt(results[1]) //first group
    //fixme add checks for negative or floating point hours and mins
    const mins = parseInt(results[2]);
    const secs = parseFloat(results[3]);

    const answer = dms2real(degs, mins, secs);
    return answer;
}

// convert right ascension (numbers: hours, minutes, secs) to decimal degrees as real
export function ra2real( hr:number, min:number, sec:number ) : number {
    return 15.0*(hr + min/60.0 + sec/3600.0);
}

// convert angle (numbers: deg, min, sec) to decimal degrees as real
function dms2real( deg:number, min:number, sec:number ) : number {
    let rv;

    if (deg < 0)
        rv = deg - min/60.0 - sec/3600.0;
    else
        rv = deg + min/60.0 + sec/3600.0;

    return rv;
}

// convert RA decimal degrees to string H M S (spaces between)
// NOTE code is incorrect for West or South long and lat!
export function degree2HMS(degrees:number) : string {
    const hours_full = degrees / 15.0  //15 degrees per hour (24h = 360)
    const hours = Math.floor(hours_full)
    const mins_full = (hours_full - hours) * 60
    const mins = Math.floor(mins_full)
    const secs = Math.round((mins_full - mins) * 60)
    const reply = `${hours}H ${mins}M ${secs}S`
    return reply
}

// from ChatGPT, better code to handle West longitude - where only the degree part is negative
// also modified to drop the decimal, minute, and second symbols
export function decimalToDMS(decimalDegrees:number) {
    const sign = decimalDegrees < 0 ? -1 : 1;
    decimalDegrees = Math.abs(decimalDegrees);

    const degrees = Math.floor(decimalDegrees);
    const remainderAfterDegrees = decimalDegrees - degrees;
    const minutes = Math.floor(remainderAfterDegrees * 60);
    const remainderAfterMinutes = remainderAfterDegrees * 60 - minutes;
    //const seconds = Math.round(remainderAfterMinutes * 60);
    const seconds = (remainderAfterMinutes * 60);

    //return `${sign * degrees}° ${minutes}' ${seconds}"`;
    return `${sign * degrees} ${minutes} ${seconds.toFixed(1)}`;
}

//convert DEC degrees to string D° M' S"
// NOTE this code is probably wrong for West long and South lat!
// use decimaltoDMS above
export function degree2DMS(degrees:number) : string {
  const degs = Math.floor(degrees)
  const mins_full = (degrees - degs) * 60
  const mins = Math.floor(mins_full)
  const secs = Math.round((mins_full - mins) * 60)
  const reply = `${degs}° ${mins}' ${secs}"`
  return reply
}

// Compute the Mean Sidereal Time in units of degrees. 
// lon = longitude of the observer
// Use lon := 0 to get the Greenwich MST. 
// East longitudes are positive; West longitudes are negative
// returns: time in decimal DEGREES
// note: if lon is not zero, this returns LST, not MST (degrees, not hours)
export function mean_sidereal_time(now:Date, lon:number) : number
{
    let   year   = now.getUTCFullYear();
    let   month  = now.getUTCMonth() + 1;
    const day    = now.getUTCDate();
    const hour   = now.getUTCHours();
    const minute = now.getUTCMinutes();
    const second = now.getUTCSeconds();

    if ((month == 1)||(month == 2))
    {
        year  = year - 1;
        month = month + 12;
    }

    const a = Math.floor(year/100);
    const b = 2 - a + Math.floor(a/4);
    const c = Math.floor(365.25*year);
    const d = Math.floor(30.6001*(month + 1));

    // days since J2000.0
    const jd = b + c + d - 730550.5 + day + (hour + minute/60.0 + second/3600.0)/24.0;

    // julian centuries since J2000.0
    const jt = jd/36525.0;

    // the mean sidereal time in degrees
    // note that conversion to hours you would divide by 15
    let mst = 280.46061837 + 360.98564736629*jd + 0.000387933*jt*jt - jt*jt*jt/38710000 + lon;

    // in degrees modulo 360.0
    if (mst > 0.0) 
        while (mst > 360.0) mst = mst - 360.0;
    else
        while (mst < 0.0)   mst = mst + 360.0;
        
    return mst;  //mst
}

// lat, lon = latitude, longitude of observer
// utc = time of observation in utc
// ra, dec = decimal degree coords of object
export function coord_to_horizon( utc:Date, ra:number, dec:number, lat:number, lon:number ) : [number, number]
{
    // compute hour angle in degrees
    let ha = mean_sidereal_time( utc, lon ) - ra;
    if (ha < 0) ha = ha + 360;

    // convert degrees to radians
    ha  = ha*Math.PI/180
    dec = dec*Math.PI/180
    lat = lat*Math.PI/180

    // compute altitude in radians
    const sin_alt = Math.sin(dec)*Math.sin(lat) + Math.cos(dec)*Math.cos(lat)*Math.cos(ha);
    const alt = Math.asin(sin_alt);
    
    // compute azimuth in radians
    // divide by zero error at poles or if alt = 90 deg
    const cos_az = (Math.sin(dec) - Math.sin(alt)*Math.sin(lat))/(Math.cos(alt)*Math.cos(lat));
    const az  = Math.acos(cos_az);

    // convert radians to degrees
    const hrz_altitude = alt*180/Math.PI;
    let hrz_azimuth  = az*180/Math.PI;

    // choose hemisphere
    if (Math.sin(ha) > 0) hrz_azimuth = 360 - hrz_azimuth;

    return([hrz_altitude, hrz_azimuth]);
}

// code from d3-celestial (https://github.com/ofrohn/d3-celestial)
// given horizontal coordinates, return ra, dec
// horizontal.inverse = function(dt, hor, loc) {
  
//     var alt = hor[0] * deg2rad;
//     var az = hor[1] * deg2rad;
//     var lat = loc[0] * deg2rad;
     
//     var dec = Math.asin((Math.sin(alt) * Math.sin(lat)) + (Math.cos(alt) * Math.cos(lat) * Math.cos(az)));
//     var ha = ((Math.sin(alt) - (Math.sin(dec) * Math.sin(lat))) / (Math.cos(dec) * Math.cos(lat))).toFixed(6);
    
//     ha = Math.acos(ha);
//     ha  = ha / deg2rad;
    
//     var ra = getMST(dt, loc[1]) - ha;
//     //if (ra < 0) ra = ra + 360;
      
//     return [ra, dec / deg2rad, 0];
//   };

export function horiz_to_equitorial(obs_dt:Date, alt:number, azi: number, obs_long:number, obs_lat:number ): [number, number] {
    // convert horizontal coords to equitorial coords
    // alt, azi in degrees
    // obs_long, obs_lat in degrees
    // returns ra, dec in degrees

    // console.log("horiz_to_equitorial", obs_dt, alt, azi, obs_long, obs_lat);
    const deg2rad = Math.PI/180

    const alt_rad = alt * deg2rad
    const azi_rad = azi * deg2rad
    const obs_lat_rad = obs_lat * deg2rad

    // arcsin takes and returns radians not degrees
    const dec_rad  = Math.asin((Math.sin(alt_rad) * Math.sin(obs_lat_rad)) + 
                    (Math.cos(alt_rad) * Math.cos(obs_lat_rad) * Math.cos(azi_rad)));
    //console.log("dec vs dec2", dec, dec2)

    let ha = ((Math.sin(alt_rad) - (Math.sin(dec_rad) * Math.sin(obs_lat_rad))) / (Math.cos(dec_rad) * Math.cos(obs_lat_rad)));
    // const ha2 = ha
    // ha cant be greater than 1 or less than -1
    ha = ha > 1 ? 1.0 : ha;
    ha = ha < -1 ? -1.0 : ha;
    const ha_rad = Math.acos(ha);
    const ha_deg  = ha_rad / deg2rad; // convert to degrees

    // console.log("HtoEQ: ha, ha_acos, ha_deg", ha, Math.acos(ha), Math.acos(ha)/deg2rad  )
    
    const lst_degrees = mean_sidereal_time(obs_dt, obs_long);
    
    // console.log("For azi=", azi, "ha deg = ", ha.toFixed(3), " orig ha = ", ha2.toFixed(3), "dec deg = ", (dec_rad/deg2rad).toFixed(3), " ra hrs = ", (lst_degrees - ha)/15)
    let ra;
    // dpm added this to make it reflect around the meridian - not sure why it didn't already do this?
    if (azi > 180) {
        ra = lst_degrees - ha_deg;
    } else {
        ra = lst_degrees + ha_deg;
    }
    //let ra = lst_degrees - ha; // degrees
    //if (ra < 0) ra = ra + 360;

    //cosh = ((Math.sin(alt_rad) - (Math.sin(dec) * Math.sin(obs_lat_rad))) / (Math.cos(dec) * Math.cos(obs_lat_rad)));
    
    //let h = Math.acos(cosh); // acos returns radians
    
    //h  = h / deg2rad;
    //h = h / 15; // convert to hours

    // let ra = mean_sidereal_time(obs_dt, obs_long) - ha;
    // if (ra < 0) ra = ra + 360;

    //const ra = (mean_sidereal_time(obs_dt, obs_long)- h) /15;
    return [ra , dec_rad / deg2rad]; // returns degrees and degrees

}

function hrsToHMS(hrs:number):string {
    //convert decimal hours to H M S
    const h = Math.floor(hrs)
    const m = Math.floor((hrs - h) * 60)
    const s = Math.round(((hrs - h) * 60 - m) * 60)
    const reply = `${h}H ${m}M ${s}S`
    return reply
}
export function get_transit_time(utc:Date, ra:number, dec:number, lat:number, lon:number) : Date {  
    
    // get local sidereal time in degrees, cvt to hours
    // const lst = mean_sidereal_time(utc, lon);
    // const lst_hrs = lst/15.0;
    const lst_hrs = gpt_localSiderealTime(utc, lon)
    console.log("GTT: lmst: " + lst_hrs + " hours, based on long = " + lon + " degrees")

    const ra_hours = ra/15.0;
    console.log("GTT: ra in hours: " + ra_hours + " hours", ra)

    // compute time difference between RA and LST
    let td_hours = ra_hours - lst_hrs;
    if (td_hours < 0) td_hours = td_hours + 24;

    console.log("GTT: hour angle hours = ", lst_hrs - ra_hours, " or HMS = ", hrsToHMS(Math.abs(lst_hrs - ra_hours)) )

    console.log("GTT: time diff hours: " + td_hours + " hours")    

    // convert sidereal hours to regular hours by divide by sid/solor ratio 1.00273790935
    // this gives transit time in ref to LST
    const transit_hours = td_hours / 1.00273790935;
    console.log("GTT: solar transit Hours: " + transit_hours + " hours")

    // Get the current local civil time
    // FIXME is this server safe???
    const now = new Date();
    const localHours = now.getHours() + now.getMinutes() / 60 + now.getSeconds() / 3600;
    console.log("GTT: localHours: " + localHours + " hours based on local time = " + now.toLocaleString())

    // Calculate the difference between local civil time and LST
    let localCivilTimeToLSTDiff = localHours - lst_hrs;
    console.log("GTT: localCivilTimeToLSTDiff: " + localCivilTimeToLSTDiff + " hours")

    // Adjust the difference to the range between -12 and +12 hours
    if (localCivilTimeToLSTDiff > 12) {
        localCivilTimeToLSTDiff -= 24;
    } else if (localCivilTimeToLSTDiff < -12) {
        localCivilTimeToLSTDiff += 24;
    }

    // Add the difference to the transit_time_local
    const transitTimeCivil = transit_hours + localCivilTimeToLSTDiff;
    console.log("GTT: transitTimeCivil: " + transitTimeCivil + " hours")

    // Ensure the transit time is within the range of 0 to 24 hours
    const transitTimeCivilAdjusted = ((transitTimeCivil % 24) + 24) % 24;

    // Split transit time into hours, minutes, and seconds
    const transitHours = Math.floor(transitTimeCivilAdjusted);
    const transitMinutes = Math.floor((transitTimeCivilAdjusted * 60) % 60);
    const transitSeconds = Math.floor((transitTimeCivilAdjusted * 3600) % 60);

    console.log(`GTT: Transit Time (Civil): ${transitHours}h ${transitMinutes}m ${transitSeconds}s`);
    console.log(`GTT: ALTERNATE Transit Time (Civil): ${hrsToHMS(transitTimeCivilAdjusted)}}`);

    return new Date(utc.getFullYear(), utc.getMonth(), utc.getDate(), transitHours, transitMinutes, transitSeconds);
}

// from GPT
function gpt_jd(date:Date) {
    const year = date.getUTCFullYear();
    const month = date.getUTCMonth() + 1;
    const day = date.getUTCDate();
    const hour = date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600;
  
    const a = Math.floor((14 - month) / 12);
    const y = year + 4800 - a;
    const m = month + 12 * a - 3;
    const jdn = day + Math.floor((153 * m + 2) / 5) + 365 * y + Math.floor(y / 4) - Math.floor(y / 100) + Math.floor(y / 400) - 32045;
    const jd = jdn + hour / 24 - 0.5;
    console.log("GPT's jd: " + jd);
    return jd;
  }
  
  // from GPT
  function gpt_gmst(julianDate:number) {
    const T = (julianDate - 2451545.0) / 36525;
    const gmst_degrees = (280.46061837 + 360.98564736629 * (julianDate - 2451545.0) + 0.000387933 * T * T - T * T * T / 38710000) % 360;
    const gmst_hours = gmst_degrees / 15;
    console.log("GPT's gmst: " + gmst_hours + " hours")
    return gmst_hours;
  }
  
  // from GPT
  // local mean sidereal time, in hours
  export function gpt_localSiderealTime(date: Date, observerLongitude:number):number {
    // const now = new Date();
    //const now = date;
    const julianDate = gpt_jd(date);
    const gmstHours = gpt_gmst(julianDate);
  
    const longitudeHours = observerLongitude / 15.0;
    let lst = gmstHours + longitudeHours;
  
    // Adjust LST to the range of 0 to 24 hours
    lst = ((lst % 24) + 24) % 24;

    const lstHours = Math.floor(lst);
    const lstMinutes = Math.floor((lst * 60) % 60);
    const lstSeconds = Math.floor((lst * 3600) % 60);

    console.log("GPT's localSiderealTime: " + lst + " hours") 
    console.log(`GPT's Local Sidereal Time: ${lstHours}h ${lstMinutes}m ${lstSeconds}s`);
    return lst;
  }
  
  // from GPT
  // ra will be in decimal degress
  function gpt_calculateTransitTime(date:Date, lst:number, ra:number) {

    const ra_hours = ra / 15.0;

    console.log("calc transit time starts with ra_hours: " + hrsToHMS(ra_hours) + " hours and lst: " + hrsToHMS(lst) + " hours")
    // Calculate the time difference between the object's RA and the current LST
    // measuring HA as west of the meridian is positive, east of the meridian is negative
    // RAs of items farther east will be larger numbers
    const timeDifference = lst - ra_hours;  // same as hour angle. Negative means east of meridian, positive means west of meridian
    const direction = timeDifference < 0 ? 'east' : 'west';
    const timeDifferenceAbs = Math.abs(timeDifference);
  
    const transitTimeLST = (ra + 24 - lst) % 24;
    
    // If the time difference is negative, add 24 hours to get a positive value
    // if (timeDifference < 0) {
    //   timeDifference += 24;
    // }
  
    // The time difference now represents the time it will take for the object to transit (cross the meridian) in sidereal hours.
    // Convert this value to local hours by dividing it by the ratio of sidereal to solar time.
    const timeDifferenceAbsLocal = timeDifferenceAbs  // / 1.00273790935;
  
    console.log("Hour angle: " + hrsToHMS(timeDifferenceAbsLocal) + " hours, " + direction + " of meridian")
    console.log("transitTimeLST: " + hrsToHMS(transitTimeLST) )

    // Convert the LST to local civil time
    // const now = new Date();

    const localHours = date.getHours() + date.getMinutes() / 60 + date.getSeconds() / 3600;
    
    let transitTimeCivil: number;

    if (direction === 'east') {
        transitTimeCivil =  localHours + timeDifferenceAbsLocal;
    } else {
        transitTimeCivil = localHours - timeDifferenceAbsLocal;
    }
    console.log("pre-adjustment (next day?) transitTimeCivil: " + transitTimeCivil + " hours")
    //const localCivilTimeToLSTDiff = localHours - lst;
  
    // Add the difference to the transit_time_local to get the transit time in local civil time
    //const transitTimeCivil = transitTimeLocal + localCivilTimeToLSTDiff;
  
    // Ensure the transit time is within the range of 0 to 24 hours
    const transitTimeCivilAdjusted = ((transitTimeCivil % 24) + 24) % 24;
  
    return transitTimeCivilAdjusted;
  }
  
  export function use_gpt_calculateTransitTime(date:Date, ra:number, observerLongitude:number): Date {
    
    console.log("called with date: " + date + " ra: " + ra + " observerLongitude: " + observerLongitude )
    const lst = gpt_localSiderealTime(date, observerLongitude);
    const oldlst = mean_sidereal_time(date, observerLongitude ) / 15.0
    console.log("old lst: " + hrsToHMS(oldlst) + " new lst: " + hrsToHMS(lst))
    const transitTimeCivilAdjusted = gpt_calculateTransitTime(date, oldlst, ra);
    return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 
        Math.floor(transitTimeCivilAdjusted), Math.floor((transitTimeCivilAdjusted * 60) % 60), Math.floor((transitTimeCivilAdjusted * 3600) % 60));
  }

    // from GPT
    // not need for fastHTML version
// function calculateRiseSetTimes(ra, dec, observerLatitude, observerLongitude) {
//     // Calculate observer's LST at 0h local time on the desired date
//     const lstAtMidnight = localSiderealTime(observerLongitude); // Using the localSiderealTime function from previous answer
  
//     // Calculate the Hour Angle (HA) at the time of the object's rise and set
//     const cosHA = (Math.sin(deg2rad(-0.5667)) - Math.sin(deg2rad(observerLatitude)) * Math.sin(deg2rad(dec))) / (Math.cos(deg2rad(observerLatitude)) * Math.cos(deg2rad(dec)));
//     const HA = rad2deg(Math.acos(cosHA)) / 15; // Convert to hours
  
//     // Calculate the transit time of the object (when it crosses the meridian)
//     const transitTimeLST = (ra + 24 - lstAtMidnight) % 24;
  
//     // Calculate the rise and set times in terms of Local Sidereal Time
//     const riseTimeLST = (transitTimeLST - HA + 24) % 24;
//     const setTimeLST = (transitTimeLST + HA) % 24;
  
//     // Convert rise and set times from LST to local civil time
//     const riseTimeLocal = lstToCivilTime(riseTimeLST, observerLongitude); 
//     const setTimeLocal = lstToCivilTime(setTimeLST, observerLongitude); 
  
//     return { riseTimeLocal, setTimeLocal };
//   }

// ra and dec are decimal degrees of DSO
// lat, long, and utc refer to observer
// lat, long are in DMS form (e.g.  +/-41° 26" 13.2')

export function altitude_of_dso(utc: Date, ra: number, dec: number, 
                    lat_dms: string, lon_dms: string) {

    const lat = parseDEC(lat_dms)
    const lon = parseDEC(lon_dms)
  
    return coord_to_horizon( utc, ra, dec, lat, lon )[0]; //alt = first element only
  }

// add number of hours to the passed in Date
// stackoverflow
export function addHours(date:Date, hours:number) : Date{
    const result = new Date(date);
    result.setHours(result.getHours() + hours);
    return result;
}

// add number of days to passed in Date
export function addDays(date:Date, days:number) : Date {
    const result = new Date(date);
    result.setDate(result.getDate() + days);
    return result;
}

export function generate_sample_dates_by_hour(start_date:Date, hours_interval:number, intervals:number) {
    const sample_dates:Array<Date> = []

    // assumes start_date is specified to the day and hour
    const target_date = start_date //new Date(start_day + " " + start_hour) //concatenate the strings and let Date() parse figure it out!
  
    for (let i=0; i<intervals; i++) {  
      sample_dates.push(addHours(target_date, i * hours_interval)) //add days does the right math for Date objects
    }
    return sample_dates
  }
  