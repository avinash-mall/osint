import { useEffect, useState } from 'react';
import { MapContainer, ImageOverlay, Marker, Popup, ZoomControl, Polyline, Circle } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';
import { Crosshair, Navigation, ShieldAlert, Activity } from 'lucide-react';

// Removed external CDN merge for offline support
delete (L.Icon.Default.prototype as any)._getIconUrl;

// Custom Icons
const createIcon = (color: string) => new L.Icon({
  iconUrl: `data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIlMjMzYjgyZjYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgMTBsLTkgMTVMMyAxMGw5LTl6Ii8+PC9zdmc+`.replace('%233b82f6', encodeURIComponent(color)),
  iconSize: [20, 20],
  iconAnchor: [10, 20],
  popupAnchor: [0, -20],
});

const blueIcon = createIcon('#3b82f6');
const redIcon = createIcon('#ef4444');
const emeraldIcon = createIcon('#10b981');

export default function GaiaMap() {
  const [data, setData] = useState<{ static: any[], tracks: any[] }>({ static: [], tracks: [] });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
        const response = await axios.get(`${apiUrl}/api/geotime/features`);
        setData(response.data || { static: [], tracks: [] });
      } catch (error) {
        console.error("Error fetching geotime data:", error);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 10000); // Polling for updates
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="w-full h-full relative bg-slate-950 flex flex-col">
      {/* Top Overlay Panel */}
      <div className="absolute top-4 left-4 z-[400] bg-slate-900/90 p-4 rounded border border-slate-700 backdrop-blur-md shadow-2xl w-64 pointer-events-auto">
        <h2 className="text-slate-100 font-bold tracking-widest text-sm mb-3 flex items-center gap-2 uppercase">
          <Activity className="w-4 h-4 text-emerald-500" /> Gaia Geo-Spatial
        </h2>
        <div className="flex flex-col gap-2">
          <div className="flex justify-between items-center p-2 bg-slate-800/50 rounded border border-slate-800">
             <span className="text-xs text-slate-400 font-semibold flex items-center gap-2"><Navigation className="w-3 h-3 text-blue-400"/> ACTIVE TRACKS</span>
             <span className="text-sm font-mono text-slate-200">{data.tracks.length}</span>
          </div>
          <div className="flex justify-between items-center p-2 bg-slate-800/50 rounded border border-slate-800">
             <span className="text-xs text-slate-400 font-semibold flex items-center gap-2"><ShieldAlert className="w-3 h-3 text-red-400"/> LAUNCH POINTS</span>
             <span className="text-sm font-mono text-slate-200">{data.static.filter(s => s.label === 'LaunchPoint').length}</span>
          </div>
        </div>
      </div>

      <div className="flex-1 relative">
        <MapContainer 
          center={[25.0, 55.0]} 
          zoom={6} 
          style={{ height: '100%', width: '100%', background: '#020617' }}
          zoomControl={false}
        >
          <ZoomControl position="bottomright" />
          <ImageOverlay
            url="/world_map.svg"
            bounds={[[-90, -180], [90, 180]]}
            opacity={0.3}
          />

          {/* Render Static Features & Range Rings */}
          {data.static.map((loc) => {
            const isLaunchPoint = loc.label === 'LaunchPoint';
            const radius = loc.properties.threatRadius || 0;
            return (
              <div key={loc.id}>
                {isLaunchPoint && radius > 0 && (
                   <Circle 
                     center={[loc.properties.latitude, loc.properties.longitude]}
                     radius={radius}
                     pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.1, weight: 1, dashArray: '5, 5' }}
                   />
                )}
                <Marker 
                  position={[loc.properties.latitude, loc.properties.longitude]}
                  icon={isLaunchPoint ? redIcon : emeraldIcon}
                >
                  <Popup className="gotham-popup">
                    <div className="text-slate-200 bg-slate-900 p-2 rounded shadow-xl border border-slate-700 min-w-[200px]">
                      <h3 className="font-bold text-sm tracking-wider uppercase flex items-center gap-2 border-b border-slate-700 pb-1 mb-2">
                        {isLaunchPoint ? <ShieldAlert className="w-4 h-4 text-red-500" /> : <Crosshair className="w-4 h-4 text-emerald-500" />}
                        {loc.properties.name}
                      </h3>
                      <div className="text-xs font-mono text-slate-400 flex flex-col gap-1">
                        <span className="flex justify-between"><span>LAT:</span> <span className="text-slate-200">{loc.properties.latitude.toFixed(4)}</span></span>
                        <span className="flex justify-between"><span>LON:</span> <span className="text-slate-200">{loc.properties.longitude.toFixed(4)}</span></span>
                        {radius > 0 && <span className="flex justify-between"><span>RANGE:</span> <span className="text-red-400">{radius/1000}km</span></span>}
                      </div>
                    </div>
                  </Popup>
                </Marker>
              </div>
            );
          })}

          {/* Render Tracks */}
          {data.tracks.map((track) => {
             const positions: [number, number][] = track.history.map((h: any) => [h.lat, h.lng]);
             const latest = track.latest;
             return (
               <div key={track.id}>
                 <Polyline 
                   positions={positions} 
                   pathOptions={{ color: '#3b82f6', weight: 2, opacity: 0.5, dashArray: '4, 6' }} 
                 />
                 {latest && (
                   <Marker position={[latest.latitude, latest.longitude]} icon={blueIcon}>
                     <Popup className="gotham-popup">
                        <div className="text-slate-200 bg-slate-900 p-2 rounded shadow-xl border border-slate-700 min-w-[200px]">
                          <h3 className="font-bold text-sm tracking-wider uppercase flex items-center gap-2 border-b border-slate-700 pb-1 mb-2">
                            <Navigation className="w-4 h-4 text-blue-500" />
                            {track.properties.callsign || track.asset_id}
                          </h3>
                          <div className="text-xs font-mono text-slate-400 flex flex-col gap-1">
                            <span className="flex justify-between"><span>TYPE:</span> <span className="text-slate-200">{track.label}</span></span>
                            <span className="flex justify-between"><span>SPEED:</span> <span className="text-slate-200">{track.properties.speed?.toFixed(1)} kts</span></span>
                            <span className="flex justify-between"><span>HDG:</span> <span className="text-slate-200">{latest.heading?.toFixed(0)}°</span></span>
                            <span className="flex justify-between"><span>LAT:</span> <span className="text-slate-200">{latest.latitude.toFixed(4)}</span></span>
                            <span className="flex justify-between"><span>LON:</span> <span className="text-slate-200">{latest.longitude.toFixed(4)}</span></span>
                          </div>
                        </div>
                     </Popup>
                   </Marker>
                 )}
               </div>
             )
          })}
        </MapContainer>
      </div>

      {/* Time Slider Panel Placeholder */}
      <div className="h-20 bg-slate-900 border-t border-slate-800 p-4 flex items-center gap-4">
         <div className="text-xs font-bold text-slate-400 tracking-wider w-24">TIMELINE</div>
         <div className="flex-1 h-2 bg-slate-800 rounded relative">
           <div className="absolute top-0 left-1/4 right-0 bottom-0 bg-blue-500/30 rounded border border-blue-500"></div>
         </div>
         <div className="text-xs font-mono text-blue-400">{new Date().toISOString()}</div>
      </div>
    </div>
  );
}
