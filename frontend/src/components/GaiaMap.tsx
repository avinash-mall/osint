import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, ZoomControl } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';

// Fix Leaflet's default icon path issues in React
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// A custom Gotham-style dark icon using a blue marker
const gothamIcon = new L.Icon({
  iconUrl: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjM2I4MmY2IiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTIxIDEwbC05IDE1TDMgMTBsOS05eiIvPjwvc3ZnPg==',
  iconSize: [24, 24],
  iconAnchor: [12, 24],
  popupAnchor: [0, -24],
});

export default function GaiaMap() {
  const [locations, setLocations] = useState<any[]>([]);

  useEffect(() => {
    const fetchLocations = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
        const response = await axios.get(`${apiUrl}/api/geospatial`);
        setLocations(response.data.locations || []);
      } catch (error) {
        console.error("Error fetching geospatial data:", error);
      }
    };
    fetchLocations();
  }, []);

  return (
    <div className="w-full h-full relative">
       <div className="absolute top-4 left-4 z-[400] bg-slate-800/80 p-4 rounded border border-slate-700 backdrop-blur-sm shadow-lg">
        <h2 className="text-slate-200 font-semibold mb-2 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_#10b981]"></span> Gaia Spatial
        </h2>
        <div className="text-xs text-slate-400 font-mono">
          TRACKED ENTITIES: <span className="text-emerald-400">{locations.length}</span>
        </div>
      </div>
      <MapContainer 
        center={[36.0, -96.0]} 
        zoom={4} 
        style={{ height: '100%', width: '100%', background: '#0f172a' }}
        zoomControl={false}
      >
        <ZoomControl position="bottomright" />
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution='&copy; CARTO'
        />
        {locations.map((loc) => (
          <Marker 
            key={loc.id} 
            position={[loc.properties.latitude, loc.properties.longitude]}
            icon={gothamIcon}
          >
            <Popup>
              <div className="text-slate-800 p-1">
                <h3 className="font-bold">{loc.properties.name}</h3>
                <div className="mt-1 text-xs font-mono">
                  LAT: {loc.properties.latitude.toFixed(4)}<br/>
                  LON: {loc.properties.longitude.toFixed(4)}
                </div>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
}
