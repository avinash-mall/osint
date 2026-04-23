import { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import Globe from 'react-globe.gl';
import { Satellite, Radio, Wifi, Map as MapIcon, Globe2, ScanEye } from 'lucide-react';

export default function ConstellationView() {
  const [satellites, setSatellites] = useState<any[]>([]);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const containerRef = useRef<HTMLDivElement>(null);
  const globeRef = useRef<any>(null);

  useEffect(() => {
    const resizeObserver = new ResizeObserver(entries => {
      if (entries[0]) {
        setDimensions({
          width: entries[0].contentRect.width,
          height: entries[0].contentRect.height
        });
      }
    });
    if (containerRef.current) resizeObserver.observe(containerRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    const fetchSats = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
        const response = await axios.get(`${apiUrl}/api/constellation`);
        // Transform for globe
        const satData = response.data.satellites.map((s: any) => ({
          lat: s.properties.lat,
          lng: s.properties.lon,
          alt: s.properties.orbit_alt / 1000, // Normalize altitude
          name: s.properties.name,
          type: s.properties.type,
          status: s.properties.status,
          color: s.properties.type === 'Optical' ? '#3b82f6' : 
                 s.properties.type === 'Radar' ? '#ef4444' : '#10b981'
        }));
        setSatellites(satData);
      } catch (error) {
        console.error("Error fetching constellation data:", error);
      }
    };
    fetchSats();
  }, []);

  useEffect(() => {
    if (globeRef.current) {
      globeRef.current.controls().autoRotate = true;
      globeRef.current.controls().autoRotateSpeed = 0.5;
      globeRef.current.pointOfView({ altitude: 2.5 });
    }
  }, [globeRef.current]);

  const arcsData = satellites.map(sat => ({
    startLat: sat.lat,
    startLng: sat.lng,
    endLat: sat.lat + 10, // Simulated future position
    endLng: sat.lng + 20,
    color: sat.color
  }));

  return (
    <div className="w-full h-full flex bg-slate-950 text-slate-200">
      
      {/* Sidebar Panel */}
      <div className="w-80 bg-slate-900 border-r border-slate-800 flex flex-col z-10 shadow-2xl relative">
        <div className="p-6 border-b border-slate-800">
          <h2 className="text-sm font-bold tracking-widest uppercase flex items-center gap-3 mb-2">
            <Globe2 className="w-5 h-5 text-blue-500" /> Space Domain
          </h2>
          <p className="text-xs text-slate-500 font-mono">Orbital Asset Tracking & Collection</p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
          <h3 className="text-xs font-semibold text-slate-400 mb-4 tracking-widest flex items-center gap-2">
            <ScanEye className="w-3 h-3" /> NEXT COLLECTION WINDOWS
          </h3>
          
          <div className="flex flex-col gap-3">
            {satellites.map((sat, i) => (
              <div key={i} className="bg-slate-950 p-3 rounded border border-slate-800 hover:border-slate-600 cursor-pointer transition">
                 <div className="flex justify-between items-start mb-2">
                   <div className="font-bold text-sm text-slate-200">{sat.name}</div>
                   <div className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{sat.status}</div>
                 </div>
                 <div className="grid grid-cols-2 gap-2 text-xs font-mono text-slate-500">
                   <div>TYPE: <span style={{color: sat.color}}>{sat.type}</span></div>
                   <div>ALT: <span className="text-slate-300">{sat.alt * 1000}km</span></div>
                   <div>TCA: <span className="text-blue-400">T-{15 + i*12}m</span></div>
                 </div>
              </div>
            ))}
            
            {satellites.length === 0 && <div className="text-xs text-slate-500 font-mono">AWAITING TELEMETRY...</div>}
          </div>
        </div>
      </div>

      {/* 3D Globe Area */}
      <div className="flex-1 relative overflow-hidden" ref={containerRef}>
        <div className="absolute top-4 right-4 z-[400] bg-slate-900/80 p-3 rounded border border-slate-800 backdrop-blur-md">
          <div className="flex items-center gap-4 text-xs font-mono text-slate-400">
            <div className="flex items-center gap-2"><Satellite className="w-3 h-3 text-blue-500"/> OPTICAL</div>
            <div className="flex items-center gap-2"><Radio className="w-3 h-3 text-emerald-500"/> SIGINT</div>
            <div className="flex items-center gap-2"><Wifi className="w-3 h-3 text-red-500"/> RADAR</div>
          </div>
        </div>

        {dimensions.width > 0 && (
          <Globe
            ref={globeRef}
            width={dimensions.width}
            height={dimensions.height}
            globeImageUrl="/earth-night.jpg"
            backgroundImageUrl="/night-sky.png"
            labelsData={satellites}
            labelLat={d => d.lat}
            labelLng={d => d.lng}
            labelAltitude={d => d.alt}
            labelDotRadius={0.4}
            labelDotOrientation={() => 'bottom'}
            labelColor={d => d.color}
            labelText="name"
            labelSize={1.5}
            labelResolution={2}
            arcsData={arcsData}
            arcStartLat={d => d.startLat}
            arcStartLng={d => d.startLng}
            arcEndLat={d => d.endLat}
            arcEndLng={d => d.endLng}
            arcColor="color"
            arcDashLength={0.4}
            arcDashGap={0.2}
            arcDashAnimateTime={2000}
            arcAltitude={0.3}
          />
        )}
      </div>
    </div>
  );
}
