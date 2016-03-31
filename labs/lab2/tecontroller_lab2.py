#!/usr/bin/python
from tecontroller.loadbalancer.simplepathlb import SimplePathLB
from tecontroller.linkmonitor.linksmonitor_thread import LinksMonitorThread
from fibbingnode.misc.mininetlib import get_logger
from tecontroller.res import defaultconf as dconf
from tecontroller.res.problib import ProbabiliyCalculator
import threading
import time
import Queue
import itertools as it

log = get_logger()
lineend = "-"*100+'\n'

#ALGORITHM = 'exact'
ALGORITHM = 'sampled'

class TEControllerLab2(SimplePathLB):
    def __init__(self, probabilityAlgorithm='simplified', congestionThreshold = 0.95):
        
        # Call init method from LBController
        super(TEControllerLab2, self).__init__()

        # Instantiate probability calculator object
        self.pc = ProbabiliyCalculator()

        # Create lock for synchronization on accessing self.cg
        self.capacityGraphLock = threading.Lock()

        # Graph that will hold the link available capacities
        with self.capacityGraphLock:
            self.cg = self._createCapacitiesGraph()

        # Variable where we save the last "read-out" copy of the
        # capacity graph
        self.cgc = self.cg.copy()

        # Set the congestion threshold
        self.congestionThreshold = congestionThreshold
        t = time.strftime("%H:%M:%S", time.gmtime())
        log.info("%s - Congestion Threshold is set to %.2f%% of the link\n"%(t, (self.congestionThreshold)*100.0))
        
        # Type of algorithm used to calculate congestion probability
        # in the ECMP part. It can be: simplified, exact or sampled
        self.probabilityAlgorithm = probabilityAlgorithm
        t = time.strftime("%H:%M:%S", time.gmtime())
        log.info("%s - ECMP Congestion Probability Calculation function used: %s\n"%(t, self.probabilityAlgorithm))
        
        # Start the links monitorer thread linked to the event queue
        lmt = LinksMonitorThread(capacity_graph = self.cg,
                                 lock = self.capacityGraphLock,
                                 logfile = dconf.LinksMonitor_LogFile,
                                 median_filter=False)
        lmt.start()
        
    def run(self):
        """Main loop that deals with new incoming events
        """
        while not self.isStopped():
            # Get event from the queue (blocking)
            event = self.eventQueue.get()
                        
            if event['type'] == 'newFlowStarted':
                # Log it
                log.info(lineend)
                t = time.strftime("%H:%M:%S", time.gmtime())
                log.info("%s - run(): %s retrieved from eventQueue\n"%(t, event['type']))
                flow = event['data']
                log.info("\t* Flow: %s\n"%self.toLogFlowNames(flow))

                # We force that upon dealing with flow, the self.dags
                # and self.flow_allocation dictionaries are not
                # modified.
                with self.dagsLock:
                    with self.flowAllocationLock:
                        # Deal with new flow                    
                        self.dealWithNewFlow(flow)
                    
            else:
                t = time.strftime("%H:%M:%S", time.gmtime())
                log.info("%s - run(): UNKNOWN Event\n"%t)
                log.info("\t* Event: "%str(event))

    def dealWithNewFlow(self, flow):
        """
        Re-writes the parent class method.
        """
        # We acquire the lock now, and assume that capacities are
        # fixed for all execution of the dealWithNewFlow function
        with self.capacityGraphLock:
            # Make copy of the capacity graph at that moment in time
            # and release the lock.
            self.cgc = self.cg.copy()

        t = time.strftime("%H:%M:%S", time.gmtime())
        log.info("%s - Copy of the capacity graph done. Edges available capacities:\n"%t)
        for (x, y, data) in self.toLogDagNames(self.cgc).edges(data=True):
            log.info("\t(%s, %s) -> %s\n"%(x, y, data['capacity']))

        # Get the communicating interfaces
        src_iface = flow['src']
        dst_iface = flow['dst']
                
        # Get host ip's
        src_ip = src_iface.ip
        dst_ip = dst_iface.ip
                
        # Get their correspoding networks
        src_network = src_iface.network
        dst_network = self.getCurrentOSPFPrefix(dst_iface.compressed)
    
        # Get the string-type prefixes
        src_prefix = src_network.compressed
        dst_prefix = dst_network.compressed
        log.info("\t* Flow matches the following OSPF advertized prefix: %s\n"%str(dst_prefix))                
            
        # Get current Active DAG for prefix
        adag = self.getActiveDag(dst_prefix)
        log.info("\t* Active DAG for %s: %s\n"%(dst_prefix, self.toLogDagNames(adag).edges()))

        # Get the current path from source to destination
        currentPaths = self.getActivePaths(src_iface, dst_iface, dst_prefix)
        log.info("\t* Current paths: %s\n"%str(self.toLogRouterNames(currentPaths)))
            
        # ECMP active?
        if len(currentPaths) > 1:
            # ECMP is happening
            ecmp_active = True
            log.info("\t* ECMP is ACTIVE in some routers in path\n")
            
        elif len(currentPaths) == 1:
            # ECMP not active
            ecmp_active = False
            log.info("\t* ECMP is NOT active\n")
            
        else:
            t = time.strftime("%H:%M:%S", time.gmtime())
            to_log = "%s - dealWithNewFlow(): ERROR. No path between src and dst\n"
            log.info(to_log%t)
            return

        if ecmp_active:
            # Calculate congestion probability
            
            # Insert current available capacities in DAG
            for (u, v, data) in adag.edges(data=True):
                cap = self.cgc[u][v]['capacity']
                data['capacity'] = cap
            
            # Get ingress and egress router
            ingress_router = currentPaths[0][0]
            egress_router = currentPaths[0][-1]

            # compute congestion probability
            t = time.strftime("%H:%M:%S", time.gmtime())
            log.info("%s - Computing flow congestion probability\n"%t)
            #log.info("\t * DAG: %s\n"%(self.toLogDagNames(adag).edges(data=True)))
            #log.info("\t * Ingress router: %s\n"%ingress_router)
            #log.info("\t * Engress router: %s\n"%egress_router)
            log.info("\t* Flow size: %d\n"%flow.size)
            log.info("\t* Equal Cost Paths: %s\n"%self.toLogRouterNames(currentPaths))
            
            with self.pc.timer as t:
                congProb = self.pc.flowCongestionProbability(adag, ingress_router,
                                                                 egress_router, flow.size)

            # Log it
            to_print = "\t* Flow will be allocated with a congestion probability of %.2f%%\n"
            log.info(to_print%(congProb*100.0))
            log.info("\t* It took %s ms to compute probabilities\n"%str(self.pc.timer.msecs))
            to_print = "\t* Paths: %s\n"
            log.info(to_print%str([self.toLogRouterNames(path) for path in currentPaths]))

            t = time.strftime("%H:%M:%S", time.gmtime())
            log.info("%s - Applying decision function...\n"%t)

            # Apply decision function
            if self.shouldDeactivateECMP(adag, currentPaths, congProb):
                # Here we have to think what to do when probability of
                # congestion is too high.
                log.info("\t* ECMP Should be de-activated!\n")
                pass
            else:
                log.info("\t* For the moment, returning always False...\n")
                
                # Allocate flow to current paths
                self.addAllocationEntry(dst_prefix, flow, currentPaths)
        else:
            # currentPath is still a list of a single list: [[A,B,C]]
            # but makes it more understandable
            currentPath = currentPaths

            # Can currentPath allocate flow w/o congestion?
            if self.canAllocateFlow(flow, currentPath):
                # No congestion. Do nothing
                t = time.strftime("%H:%M:%S", time.gmtime())
                to_log = "%s - Flow can be ALLOCATED. Path: %s, min capacity: %s\n"
                log.info(to_log%(t, self.toLogRouterNames(currentPath), self.getMinCapacity(currentPath[0])))
                
                # We just allocate the flow to the currentPath
                self.addAllocationEntry(dst_prefix, flow, currentPath)

            else:
                # Congestion created. 
                t = time.strftime("%H:%M:%S", time.gmtime())
                to_log = "%s - Flow will cause CONGESTION. Path: %s, min capacity: %s\n"
                log.info(to_log%(t, self.toLogRouterNames(currentPath), self.getMinCapacity(currentPath[0])))
                                 
                # Call the subclassed method to properly 
                # allocate flow to a congestion-free path
                self.flowAllocationAlgorithm(dst_prefix, flow, currentPath)

    def canAllocateFlow(self, flow, path_list):
        """Returns true if there is at least flow.size bandwidth available in
        all links along the path (or multiple paths in case of ECMP)
        from flow.src to src.dst,
        """
        for path in path_list:
            # Get edge with minimum capacity of the path
            ((x,y), minCap) = self.getMinCapacityEdge(path)
            bw = self.cgc[x][y].get('bw')

            currentload = (bw - minCap)/float(bw)
            if currentload > self.congestionThreshold:
                return False
            else:
                nextload = ((bw - minCap) + flow.size)/float(bw)
                if nextload > self.congestionThreshold:
                    return False
        return True

    def getMinCapacityEdge(self, path):
        caps_edges = []
        for (u,v) in zip(path[:-1], path[1:]):
            edge_data = self.cgc.get_edge_data(u, v)
            if edge_data:
                cap = edge_data.get('capacity', None)
                caps_edges.append(((u,v), cap))
        try:
            min_cap_edge = min(caps_edges, key=lambda x: x[1])
            return min_cap_edge
        
        except ValueError:
            t = time.strftime("%H:%M:%S", time.gmtime())
            log.info("%s - getMinCapacity(): ERROR: min could not be calculated\n"%t)
            log.info("Argument should be a list! (not a list of lists)")
            raise ValueError
 
    def getMinCapacity(self, path):
        """
        We overwrite the method so that capacities are now checked from the
        SNMP couters data updated by the link monitor thread.
        """
        caps_in_path = []
        for (u,v) in zip(path[:-1], path[1:]):
            edge_data = self.cgc.get_edge_data(u, v)
            if edge_data:
                cap = edge_data.get('capacity', None)
                caps_in_path.append(cap)
        try:
            mini = min(caps_in_path)
            return mini
        except ValueError:
            t = time.strftime("%H:%M:%S", time.gmtime())
            log.info("%s - getMinCapacity(): ERROR: min could not be calculated\n"%t)
            log.info("\t* Path: %s\n"%path)            
            raise ValueError

    def _createCapacitiesGraph(self):
        # Get copy of the network graph
        ng_copy = self.network_graph.copy()
        cg = self.network_graph.copy()
        
        for node in ng_copy.nodes_iter():
            if not ng_copy.is_router(node):
                cg.remove_node(node)
                
        for (x, y, edge_data) in cg.edges(data=True):
            edge_data['window'] = []
            edge_data['capacity'] = 0
        return cg

    def shouldDeactivateECMP(self, dag, currentPaths, congProb):
        """This function returns a boolean output that decides wheather we
        should deactivate ECMP.

        TODO
        """
        return False

    def getIngressRouter(self, flow):
        """
        """
        src_iface = flow['src']
        for r in self.network_graph.routers:
            src_prefix = src_iface.network.compressed
            if self.network_graph.has_successor(r, src_prefix) and self.network_graph[r][src_prefix]['fake'] == False:
                return r
        return None

    def getEgressRouter(self, flow):
        dst_iface = flow['dst']
        for r in self.network_graph.routers:
            dst_prefix = dst_iface.network.compressed
            if self.network_graph.has_successor(r, dst_prefix) and self.network_graph[r][dst_prefix]['fake'] == False:
                return r
        return None

    def _orderByCapacityLeft(self, paths):
        """Given a list of arbitrary paths. It ranks them by capacity left (or
        total edges weight).
        
        Function is implemented in TEControllerLab1
        """
        ordered_paths = []
        for path in paths:
            min_capacity = self.getMinCapacity(path)
            ordered_paths.append((path, min_capacity))

        # Now rank them from more to less capacity available
        ordered_paths = sorted(ordered_paths, key=lambda x: x[1], reverse=True)
        return ordered_paths

    def flowAllocationAlgorithm(self, dst_prefix, flow, initial_paths):
        """
        """
        t = time.strftime("%H:%M:%S", time.gmtime())
        log.info("%s - Greedy path allocation algorithm started\n"%t)

        # Get source connected router (src_cr)
        src_iface = flow['src']
        src_prefix = src_iface.network.compressed
        src_cr = [r for r in self.network_graph.routers if
                  self.network_graph.has_successor(r, src_prefix) and
                  self.network_graph[r][src_prefix]['fake'] == False][0]

        # Get current matching destination prefix
        dst_prefix = dst_prefix

        # Get current DAG for destination prefix
        cdag = self.getCurrentDag(dst_prefix)
        
        # Get required capacity
        required_capacity = flow['size']
        
        # Calculate all possible paths for flow
        all_paths = self.getAllPathsRanked(self.initial_graph, src_cr, dst_prefix, ranked_by='length')

        # Get already ongoing flows for that prefix
        ongoing_flow_allocations = self.getAllocatedFlows(dst_prefix)

        # Filter only those that are able to allocate flow + ongoing flows moved without congestion
        congestion_free_paths = [path for (path, plen) in all_paths if self.canAllocateFlow(flow, [path[:-1]]) == True]
        
        # Check if congestion free paths exist
        if len(congestion_free_paths) == 0:

            # No. So allocate it in the least congested path.
            t = time.strftime("%H:%M:%S", time.gmtime())
            log.info("%s - Flow can't be allocated in a single path\n"%t)

            self.ECMPAlgorithm(dst_prefix, flow)

        else:
            # Yes. There is a path. Let's check though if there is at least one path 
            # that when forcing new DAG does not create congestion.
        
            t = time.strftime("%H:%M:%S", time.gmtime())
            log.info("%s - Found path/s that can allocate flow\n"%t)

            path_congestion_pairs = []

            path_found = False
            for (path, plen) in all_paths:
                # Remove the destination subnet hop node from the path
                path = path[:-1]
                    
                # Create virtual copy of capacity graph
                cg_copy = self.cgc.copy()

                # Initialize accumulated required capacity
                accumulated_required_capacity = required_capacity
                accumulated_congestion = 0
                    
                # Accumulate flows that will be moved
                total_moved_flows = []
                    
                for index, node in enumerate(path[:-1]):
                    # Get flows that will be moved to path
                    moved_flows_pairs = [(f, pl) for (f, pl) in ongoing_flow_allocations for p in pl if node in p]
                    moved_flows = [f for (f,pl) in moved_flows_pairs]

                    # Accumulate the sizes of the flows that are moved to path
                    accumulated_required_capacity += sum([f.size for f in moved_flows])

                    # Virtually substract capacities from ongoing flows
                    for (f, pl) in moved_flows_pairs:
                        # Accumulate edges where capacities have to be virtually changed
                        p_edges = set()
                        action = [p_edges.update(set(zip(p[:-1], p[1:]))) for p in pl]
                        for (x,y) in list(p_edges):
                            # Add flow size back to available capacity
                            cg_copy[x][y]['capacity'] += f.size

                    # Add moved flows to total_moved_flows
                    total_moved_flows += moved_flows
                        
                    # Calculate edge required capacity
                    edge = (node, path[index+1])
                    congestion = accumulated_required_capacity - cg_copy[edge[0]][edge[1]]['capacity']

                    if congestion > 0:
                        accumulated_congestion += congestion

                if accumulated_congestion == 0:
                    # Next shortest-path that does not create congestion found!
                    path_found = True
                    chosen_path = path
                    chosen_path_moved_flows = total_moved_flows
                    break

                # Choosing this path, would create such amount of accumulated congestion
                # Append it in variable
                path_congestion_pairs.append((path, accumulated_congestion, total_moved_flows))
                
            if path_found == True:
                # Fib this new path
                log.info("\t* Found path that does not create congestion\n")
                log.info("\t* Path (ips): %s\n"%chosen_path)
                log.info("\t* Path (readable): %s\n"%str(self.toLogRouterNames(chosen_path)))

                # Get edges of new found path
                chosen_path_edges = set(zip(chosen_path[:-1], chosen_path[1:]))
                        
                # Deactivate old edges from initial path nodes (won't be
                # used anymore)
                for node in chosen_path:
                    # Get active edges of node
                    active_edges = self.getActiveEdges(cdag, node)
                    for a_e in active_edges:
                        if a_e not in chosen_path_edges:
                            cdag = self.switchDagEdgesData(cdag, [(a_e)], active = False)
                            cdag = self.switchDagEdgesData(cdag, [(a_e)], ongoing_flows = False)

                # Update the flow_allocation
                for f in chosen_path_moved_flows:
                    # Get path list of flow
                    pl = self.flow_allocation[dst_prefix].get(f)
                    
                    final_pl = []
                    # Iterate previous paths (pp) in path list
                    for pp in pl:
                        # Check if previous path has a node in common with chosen path
                        indexes = [pp.index(node) for node in pp if node in chosen_path]
                        if indexes == []:
                            final_pl.append(pp)
                        else:
                            index_pp = min(indexes)
                            index_cp = chosen_path.index(pp[index_pp])
                            final_pl.append(pp[:index_pp] + chosen_path[index_cp:])
                            
                    # Update allocation entry
                    self.flow_allocation[dst_prefix][f] = final_pl

                # Add new edges from new computed path
                cdag = self.switchDagEdgesData(cdag, [chosen_path], active=True)
                    
                # This complete DAG goes to the prefix-dag data attribute
                self.setCurrentDag(dst_prefix, cdag)
                    
                # Retrieve only the active edges to force fibbing
                final_dag = self.getActiveDag(dst_prefix)

                # Log it
                log.info("\t* Final modified dag for prefix: the one with which we fib the prefix\n")
                log.info("\t  %s\n"%str(self.toLogDagNames(final_dag).edges()))
                    
                # Force DAG for dst_prefix
                self.sbmanager.add_dag_requirement(dst_prefix, final_dag)
                    
                # Allocate flow to Path. It HAS TO BE DONE after changing the DAG...
                self.addAllocationEntry(dst_prefix, flow, [chosen_path])

                # Log 
                t = time.strftime("%H:%M:%S", time.gmtime())
                to_print = "%s - Forced forwarding DAG in Southbound Manager\n"
                log.info(to_print%t)

            else:
                self.ECMPAlgorithm(dst_prefix, flow)
    
    def ECMPAlgorithm(self, dst_prefix, flow):
        """
        """
        
        # Get flow ingress router
        ingress_rid = self.getIngressRouter(flow)

        # Get current DAG for flow
        cdag = self.getCurrentDag(dst_prefix)

        # Get already ongoing flows for dst_prefix
        prefix_ongoing_flows = self.getAllocatedFlows(dst_prefix)

        # Filter those who has same ingress router
        ongoing_flows_same_ir = [(f, pl) for (f, pl) in prefix_ongoing_flows if self.getIngressRouter(f) == ingress_rid]

        # Calculate maximum flow size and parameter n
        flow_sizes = [f.size for (f, pl) in ongoing_flows_same_ir]+[flow.size]
        
        # Create virtual copy of capacity graph
        cg_copy = self.cgc.copy()


        #import ipdb; ipdb.set_trace()
        
        # Add back capacities to their respective paths
        for f, pl in ongoing_flows_same_ir:
            # Accumulate edges where capacities have to be virtually changed
            p_edges = set()
            action = [p_edges.update(set(zip(p[:-1], p[1:]))) for p in pl]
            for (x,y) in list(p_edges):
                # Add flow size back to available capacity
                cg_copy[x][y]['capacity'] += f.size

        # Compute all loop-less different paths from src to dst
        all_paths = self.getAllPathsRanked(self.initial_graph, ingress_rid, dst_prefix, ranked_by='capacity')

        # Filter out only disjoint paths
        disjoint_paths = []
        unique_edges = set()
        for path, pcap in all_paths:
            path = path[:-1]
            # Get path edges
            path_edges = set(zip(path[:-1], path[1:]))
            # Check if collides with other paths
            if path_edges.intersection(unique_edges) == set():
                unique_edges.update(path_edges)
                # Calculate virtual minimum capacity
                pvcap = self._getVirtualMinCapacity(cg_copy, path)
                disjoint_paths.append((path, pvcap))

        # Log disjoint paths found:
        t = time.strftime("%H:%M:%S", time.gmtime())
        log.info("%s - Disjoint paths found: Path -> virtual minimum capacity\n"%t)
        for (p, pvcap) in disjoint_paths:
            log.info("\t%s\t->\t%s\n"%(str(self.toLogRouterNames(p)), str(pvcap)))

        # Log flow sizes
        log.info("%s - Flow sizes:\n"%t)
        log.info("\t%s\n"%str(flow_sizes))
            
        # Calculate all combinations of possible paths.
        all_path_subsets = []
        action = [all_path_subsets.append(c) for i in range(2, len(disjoint_paths)+1) for c in list(it.combinations(disjoint_paths, i))]

        if self.probabilityAlgorithm == 'sampled':
            chosen_paths = self.SampledProbability(all_path_subsets, flow_sizes)
            
        elif self.probabilityAlgorithm == 'exact':
            chosen_paths = self.ExactProbability(all_path_subsets, flow_sizes)
            
        else:
            chosen_paths = self.SimplifiedProbability(all_path_subsets, flow_sizes)

        # Fib chosen paths
        # Deactivate old edges from initial path nodes (won't be
        # used anymore)
        for chosen_path in chosen_paths:
            # Get edges of new found path
            chosen_path_edges = set(zip(chosen_path[:-1], chosen_path[1:]))
            
            for node in chosen_path:
                # Get active edges of node
                active_edges = self.getActiveEdges(cdag, node)
                for a_e in active_edges:
                    if a_e not in chosen_path_edges:
                        cdag = self.switchDagEdgesData(cdag, [(a_e)], active = False)
                        cdag = self.switchDagEdgesData(cdag, [(a_e)], ongoing_flows = False)

        # Update the flow_allocation
        for (f, pl) in ongoing_flows_same_ir:
            # Update allocation entry
            self.flow_allocation[dst_prefix][f] = chosen_paths

        # Add new edges from new computed path
        cdag = self.switchDagEdgesData(cdag, chosen_paths, active=True)
                    
        # This complete DAG goes to the prefix-dag data attribute
        self.setCurrentDag(dst_prefix, cdag)
                    
        # Retrieve only the active edges to force fibbing
        final_dag = self.getActiveDag(dst_prefix)

        # Log it
        log.info("\t* Final modified dag for prefix: the one with which we fib the prefix\n")
        log.info("\t  %s\n"%str(self.toLogDagNames(final_dag).edges()))
                    
        # Force DAG for dst_prefix
        self.sbmanager.add_dag_requirement(dst_prefix, final_dag)
                    
        # Allocate flow to Path. It HAS TO BE DONE after changing the DAG...
        self.addAllocationEntry(dst_prefix, flow, chosen_paths)

        # Log 
        t = time.strftime("%H:%M:%S", time.gmtime())
        to_print = "%s - Forced forwarding DAG in Southbound Manager\n"
        log.info(to_print%t)

    def _getVirtualMinCapacity(self, capacity_graph, path):
        caps = []
        for (u, v) in zip(path[:-1], path[1:]):
            caps.append(capacity_graph[u][v].get('capacity', None))
        return min(caps)
    
    def SimplifiedProbability(self, all_path_subsets, flow_sizes):
        # Get biggest flow size
        max_flow_size = max(flow_sizes)

        # Compute how many flows there are. We assume here we all have
        # max_flow_size
        n = len(flow_sizes)

        # Iterate path combinations and choose the one that minimizes
        # congestion probability
        with self.pc.timer as t:
            probs_items = []
            for path_subset in all_path_subsets:
                # Calculate minimun path capacity and parameter m
                minimum_path_capacity = min([pvcap for (path, pvcap) in path_subset])
                m = len(path_subset)
                
                # Calculate k: how many flows can each path allocate at max.
                k = int(minimum_path_capacity/max_flow_size)

                # Compute congestion probability
                congProb = self.pc.SCongestionProbability(m, n, k)

                log.info("\tPaths: %s\n"%(str(self.toLogRouterNames([p for p,c in path_subset]))))
                log.info("\tMinimun path capacity: %s\n"%(str(minimum_path_capacity)))
                log.info("\tMax flow size: %s\n"%(str(max_flow_size)))
                log.info("\tm: %d, n: %d, k: %d -> congProb: %.2f\n"%(m,n,k,congProb))
                
                # Append intermediate result
                probs_items.append((path_subset, congProb))

        # Get path subset that minimizes congestion probability
        chosen_subset = min(probs_items, key=lambda x: x[1])

        # Get the paths only
        chosen_paths = [p for (p, c) in chosen_subset[0]]

        # Get the congestion probability
        congProb_chosen_paths = chosen_subset[1]

        # Log search results
        to_log = "\t* ECMP on paths: %s minimizes the congestion probability: %.2f%%\n"
        log.info(to_log%(str(self.toLogRouterNames(chosen_paths)), congProb_chosen_paths*100))
        to_log = "\t* It took %s ms to calculate probabilities\n"
        log.info(to_log%(str(self.pc.timer.msecs)))

        return chosen_paths

    def ExactProbability(self, all_path_subsets, flow_sizes):
        """
        """
        # Iterate path combinations and choose the one that minimizes
        # congestion probability
        with self.pc.timer as t:
            probs_items = []
            for path_subset in all_path_subsets:
                # Save path capacities only
                m = [pvcap for (path, pvcap) in path_subset]

                # Save flow sizes
                n = flow_sizes

                # Compute congestion probability
                congProb = self.pc.ExactCongestionProbability(m, n)

                # Log results
                paths = [p for p,c in path_subset]
                log.info("\tPath combination:\n")
                for i, p in enumerate(paths):
                    log.info("\t\t* %s, capacity: %s\n"%(str(self.toLogRouterNames(p)), m[i]))
                log.info("\tExact congestion probability: %.2f\n\n"%(congProb))
                
                # Append intermediate result
                probs_items.append((path_subset, congProb))

        # Get path subset that minimizes congestion probability
        chosen_subset = min(probs_items, key=lambda x: x[1])

        # Get the paths only
        chosen_paths = [p for (p, c) in chosen_subset[0]]

        # Get the congestion probability
        congProb_chosen_paths = chosen_subset[1]

        # Log search results
        to_log = "\t* ECMP on paths: %s minimizes the congestion probability: %.2f%%\n"
        log.info(to_log%(str(self.toLogRouterNames(chosen_paths)), congProb_chosen_paths*100))
        to_log = "\t* It took %s ms to calculate probabilities\n"
        log.info(to_log%(str(self.pc.timer.msecs)))
        return chosen_paths
    
    def SampledProbability(self, all_path_subsets, flow_sizes):
        # Iterate path combinations and choose the one that minimizes
        # congestion probability
        with self.pc.timer as t:
            probs_items = []
            for path_subset in all_path_subsets:
                # Save path capacities only
                m = [pvcap for (path, pvcap) in path_subset]
                
                # Save flow sizes
                n = flow_sizes

                # Compute congestion probability
                (congProb, std) = self.pc.SampledCongestionProbability(m, n, percentage=30, estimate=10)

                # Log results
                paths = [p for p,c in path_subset]
                log.info("\tPath combination:\n")
                for i, p in enumerate(paths):
                    log.info("\t\t* %s, capacity: %s\n"%(str(self.toLogRouterNames(p)), m[i]))
                if not std:
                    log.info("\tExact congestion probability: %.2f\n\n"%(congProb))
                else:
                    log.info("\tExact congestion probability: %.2f +/- %.5f\n\n"%(congProb, std))
                # Append intermediate result
                probs_items.append((path_subset, congProb))

        # Get path subset that minimizes congestion probability
        chosen_subset = min(probs_items, key=lambda x: x[1])

        # Get the paths only
        chosen_paths = [p for (p, c) in chosen_subset[0]]

        # Get the congestion probability
        congProb_chosen_paths = chosen_subset[1]

        # Log search results
        to_log = "\t* ECMP on paths: %s minimizes the congestion probability: %.2f%%\n"
        log.info(to_log%(str(self.toLogRouterNames(chosen_paths)), congProb_chosen_paths*100))
        to_log = "\t* It took %s ms to calculate probabilities\n"
        log.info(to_log%(str(self.pc.timer.msecs)))
        return chosen_paths


if __name__ == '__main__':
    log.info("LOAD BALANCER CONTROLLER - Lab 2 - Enforcing simple paths + ECMP when needed\n")
    log.info("-"*90+"\n")
    time.sleep(dconf.LBC_InitialWaitingTime)
    
    tec = TEControllerLab2(probabilityAlgorithm=ALGORITHM)
    tec.run()
                                