import osmnx as ox
import networkx as nx
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Cache the graph globally to avoid re-downloading
G = None

def load_city_graph(center_lat=18.5204, center_lng=73.8567, radius_meters=15000):
    """
    Load the street network from OSMnx.
    Instead of full region downloading, we download a radius around the depot.
    """
    global G
    if G is not None:
        return G
    
    logger.info(f"Downloading OSMnx graph around {center_lat}, {center_lng} with radius {radius_meters}m...")
    try:
        # Use graph_from_point for osmnx >= 2.0 or earlier
        G = ox.graph_from_point((center_lat, center_lng), dist=radius_meters, network_type='drive')
        logger.info(f"OSMnx graph loaded successfully! Nodes: {len(G.nodes)}")
        return G
    except Exception as e:
        logger.error(f"Failed to load OSMnx graph: {e}")
        return None

def snap_to_road(lat, lng):
    """Find the nearest road node to a given lat/lng coordinate."""
    global G
    if G is None:
        load_city_graph()
    
    if G is None:
        return (lat, lng)  # fallback if graph fails

    try:
        # nearest_nodes signature osmnx >= 2.0: ox.nearest_nodes(G, X, Y) where X=lng, Y=lat
        nearest_node = ox.nearest_nodes(G, lng, lat)
        
        # Get coordinates of the snapped node
        snapped_lng = G.nodes[nearest_node]['x']
        snapped_lat = G.nodes[nearest_node]['y']
        
        return (snapped_lat, snapped_lng)
    except Exception as e:
        logger.warning(f"Error snapping to road: {e}")
        return (lat, lng)

def calculate_detailed_path(ordered_route_bins):
    """
    Given an ordered list of bins, calculate the detailed step-by-step
    lat/lng path following actual roads.
    """
    global G
    if G is None:
        load_city_graph()
        
    full_path_coords = []
    
    if G is None or len(ordered_route_bins) < 2:
        # Fallback to straight lines if OSMnx fails
        return [[b["lat"], b["lng"]] for b in ordered_route_bins]
        
    for i in range(len(ordered_route_bins) - 1):
        start_bin = ordered_route_bins[i]
        end_bin = ordered_route_bins[i + 1]
        
        try:
            node_start = ox.nearest_nodes(G, start_bin["lng"], start_bin["lat"])
            node_end = ox.nearest_nodes(G, end_bin["lng"], end_bin["lat"])
            
            # Find shortest path using Dijkstra (by length)
            path_nodes = nx.shortest_path(G, node_start, node_end, weight='length')
            
            # Convert node IDs to (lat, lng) coordinates
            segment_coords = []
            for node in path_nodes:
                segment_coords.append([G.nodes[node]['y'], G.nodes[node]['x']])
                
            # Avoid duplicating the connection node
            if i > 0 and len(segment_coords) > 0 and len(full_path_coords) > 0:
                if full_path_coords[-1] == segment_coords[0]:
                    segment_coords.pop(0)
                    
            full_path_coords.extend(segment_coords)
            
        except nx.NetworkXNoPath:
            logger.warning(f"No valid road path between {start_bin['id']} and {end_bin['id']}. Using straight line.")
            full_path_coords.append([start_bin["lat"], start_bin["lng"]])
            full_path_coords.append([end_bin["lat"], end_bin["lng"]])
        except Exception as e:
            logger.warning(f"Error calculating path segment: {e}")
            
    return full_path_coords

def build_distance_matrix(bins: list[dict], depot: dict, cap=50) -> dict:
    """
    Builds a real road distance matrix mapping id -> id -> distance_km for fast GA lookup.
    """
    global G
    if G is None:
        load_city_graph()
    
    if G is None or len(bins) > cap:
        logger.info(f"Skipping OSMnx matrix generation. Graph={G is not None}, Bins={len(bins)} (cap {cap})")
        return None # Graceful fallback to haversine if too big or graph fails

    all_nodes = [depot] + bins
    matrix = {}
    
    # Pre-map all items to OSM nodes
    node_mapping = {}
    for item in all_nodes:
        try:
            node_mapping[item['id']] = ox.nearest_nodes(G, item['lng'], item['lat'])
        except Exception:
            pass

    logger.info(f"Building OSMnx distance matrix for {len(all_nodes)} nodes...")
    for start_item in all_nodes:
        s_id = start_item['id']
        matrix[s_id] = {}
        if s_id not in node_mapping:
            continue
            
        start_node = node_mapping[s_id]
        
        # Calculate single-source shortest path lengths for extreme performance
        try:
            lengths = nx.single_source_dijkstra_path_length(G, start_node, weight='length')
            
            for end_item in all_nodes:
                e_id = end_item['id']
                if e_id not in node_mapping:
                    continue
                end_node = node_mapping[e_id]
                
                # Check if path exists
                if end_node in lengths:
                    # Convert meters to kilometers
                    matrix[s_id][e_id] = lengths[end_node] / 1000.0
                else:
                    matrix[s_id][e_id] = float('inf')
                    
        except Exception as e:
            logger.warning(f"Error computing matrix paths for {s_id}: {e}")
            
    logger.info("Distance matrix completed!")
    return matrix
