import cplex
from cplex.exceptions import CplexError
import networkx as nx
from itertools import islice

class ilp():
    def __init__(self, G=nx.Graph(), n_paths=3, qualities=[100,80,60,40], ct=0.5, cf=0, wb=0.5, wc=0.5):
        self.problem = cplex.Cplex()
        self.network = G
        self.qualities = qualities
        self.ct = ct
        self.cf = cf
        self.wb = wb
        self.wc = wc

        self.V = G.number_of_nodes()    # Number of nodes
        self.E = G.number_of_edges()
        self.K = 0
        self.M = 0
        for u in G.nodes_iter():
            self.K += len(G.node[u]['video_src'])       # Number of video resources
            self.M += len(G.node[u]['user_queries'])    # Number of queries
        self.N = n_paths        # Number of shortest paths between each pair
        self.paths = self.kshortest(self.N)
        self.Q = len(qualities) # Number of available video qualities

        """
        VMS placement         [0 ~ V-1]
        delivery tree routing [V ~ V+KNQV^2-1]
        user access param     [V+KNQV^2 ~ V+KNQV^2 + VMQ-1]
        """

    def kshortest(self, k):
        paths = [[] for _ in xrange(self.V)]

        for i, u in enumerate(self.network.nodes()):
            for j, v in enumerate(self.network.nodes()):
                if i == j:
                    paths[i].append([])
                    continue

                # get k shortest from all shortest simple paths from u to v
                # weight set to 1 to get shortest hops path
                paths[i].append(list(islice(
                    nx.shortest_simple_paths(self.network, u, v, weight='weight'),
                    k)))

        return paths

    def solve(self):
        try:
            my_prob = cplex.Cplex()
            handle = self.populate_constraints(my_prob)
            my_prob.solve()
        except CplexError, exc:
            print exc
            return

        numrows = my_prob.linear_constraints.get_num()
        numcols = my_prob.variables.get_num()

        print

        # solution.get_status() returns an integer code
        print "Solution status = ", my_prob.solution.get_status(), ":",
        # the following line prints the corresponding string
        print my_prob.solution.status[my_prob.solution.get_status()]
        print "Solution value = ", my_prob.solution.get_objective_value()
        slack = my_prob.solution.get_linear_slacks()
        pi = my_prob.solution.get_dual_values()
        x = my_prob.solution.get_values()
        dj = my_prob.solution.get_reduced_costs()
        for i in range(numrows):
            print "Row %d:  Slack = %10f    Pi = %10f" % (i, slack[i], pi[i])
        for j in range(numcols):
            print "Column %d:   Value = %10f    Reduced cost = %10f" % (j, x[j], dj[j])

        my_prob.write("ilp.lp")

    def get_gamma_column(self, src, dst, kid, nid, qid):
        return src*self.V*self.K*self.N*self.Q + \
            dst*self.K*self.N*self.Q + \
            kid*self.N*self.Q + nid*self.Q + qid + \
            self.V

    def get_alpha_column(self, from_sid, mid, qid):
        return from_sid*self.M*self.Q + mid*self.Q + qid + \
            self.V + self.V*self.V*self.K*self.N*self.Q

    def get_delta(self, access_id, query_id, content_id):
        if query_id in self.network.node[access_id]['user_queries'] and \
                self.network.node[access_id]['user_queries'][query_id] == content_id:
               return 1
        return 0

    def get_quality(self, quality_id):
        return self.qualities[quality_id]

    def get_beta(self, src_id, dst_id, link_id, path_id):
        if src_id == dst_id:
            return 0
        for u, v in self.network.edges_iter():
            if self.network.edge[u][v]['id'] == link_id:
                path = self.paths[src_id][dst_id][path_id]
                for i in xrange(len(path)-1):
                    if path[i] == u and path[i+1] == v:
                        return 1
                return 0
        return 0

    def get_bandwidth(self, link_id):
        for u, v in self.network.edges_iter():
            if self.network.edge[u][v]['id'] == link_id:
                return self.network.edge[u][v]['bandwidth']

    def get_IO(self, vms_id):
        return self.network.node[vms_id]['IO']

    def get_cloud(self, vms_id):
        return self.network.node[vms_id]['clouds']

    def get_lambda(self, vms_id, content_id):
        if content_id in self.network.node[vms_id]['video_src']:
            return 1
        else:
            return 0

    def populate_constraints(self, prob):
        rows = []
        cols = []
        vals = []
        my_rhs = []
        my_sense = ""
        row_offset = 0
        # populate row by row

        # User access constraints: Each query links to a VMS
        # constr. 1
        row_col_pair = []
        for vertex_i in range(self.V):
            for demand_i in range(self.M):

                for quality_i in range(self.Q):
                    rows.append(vertex_i*self.M+demand_i)
                    cols.append(self.get_alpha_column(vertex_i,
                                                      demand_i,
                                                      quality_i))
                    vals.append(1)
                rows.append(vertex_i*self.M+demand_i)
                cols.append(vertex_i)
                vals.append(-1)
                my_rhs.append(0)
                my_sense += "L"
        row_offset += self.V * self.M
        
        # constr. 2
        # Each query only get resource from a single VMS at a single quality
        for demand_i in range(self.M):

            for vertex_i in range(self.V):
                for quality_i in range(self.Q):
                    rows.append(row_offset + demand_i)
                    cols.append(self.get_alpha_column(vertex_i,
                                                      demand_i,
                                                      quality_i))
                    vals.append(1)
            my_rhs.append(1)
            my_sense += "E"
        row_offset += self.M

        # constr. 4
        # A user can access content stream at quality no higher than that is available at the VMS
        for demand_i in xrange(self.M):
            for video_i in xrange(self.K):
                val = 0
                for access_i in xrange(self.V):
                    val += self.get_delta(access_i, demand_i, video_i)
                for vms_i in xrange(self.V):
                    for quality_i in xrange(self.Q):
                        rows.append(row_offset + demand_i*self.K*self.V + video_i*self.V + vms_i)
                        cols.append(self.get_alpha_column(vms_i, demand_i, quality_i))
                        vals.append(val*self.get_quality(quality_i))
                    # constr. 3
                    # Intermediate variable which calculates the available highest stream quality at VMS
                    for quality_i in xrange(self.Q):
                        for src_i in xrange(self.V):
                            for path_i in xrange(self.N):
                                rows.append(row_offset + demand_i*self.K*self.V + video_i*self.V + vms_i)
                                cols.append(self.get_gamma_column(src_i, vms_i, video_i, path_i, quality_i))
                                vals.append(-self.get_quality(quality_i))
                    my_rhs.append(0)
                    my_sense += "L"
        row_offset += self.M*self.K*self.V

        # constr. 5
        # Average quality should be no smaller than a predefined rate
        for vms_i in xrange(self.V):
            for query_i in xrange(self.M):
                for quality_i in xrange(self.Q):
                    rows.append(row_offset)
                    cols.append(self.get_alpha_column(vms_i, query_i, quality_i))
                    vals.append(self.get_quality(quality_i))
        my_rhs.append(self.qualities[-1]*self.M)
        my_sense += "G"
        row_offset += 1

        # constr. 6
        for vms_i in xrange(self.V):
            for content_i in xrange(self.K):

                for path_i in xrange(self.N):
                    for src_i in xrange(self.V):
                        for quality_i in xrange(self.Q):
                            rows.append(row_offset + vms_i*self.K + content_i)
                            cols.append(self.get_gamma_column(src_i, vms_i, content_i, path_i, quality_i))
                            vals.append(1)
                rows.append(row_offset + vms_i*self.K + content_i)
                cols.append(vms_i)
                vals.append(-1)
                my_rhs.append(0)
                my_sense += "L"
        row_offset += self.V * self.K

        """
        # constr. 8
        for src_i in xrange(self.V):
            for dst_i in xrange(self.V):
                for content_i in xrange(self.K):

                    for path_i in xrange(self.N):
                        for quality_i in xrange(self.Q):
                            rows.append(row_offset + src_i*self.V*self.K + dst_i*self.K + content_i)
                            cols.append(self.get_gamma_column(src_i, dst_i, content_i, path_i, quality_i))
                            if (rows[-1], cols[-1]) in row_col_pair:
                                print "Part One!"
                                return
                            row_col_pair.append((rows[-1], cols[-1]))
                            vals.append(1)
                    # constr. 10
                    for path_i in xrange(self.N):
                        for prev_src_i in xrange(self.V):
                            for quality_i in xrange(self.Q):
                                rows.append(row_offset + src_i*self.V*self.K + dst_i*self.K + content_i)
                                cols.append(self.get_gamma_column(prev_src_i, src_i, content_i, path_i, quality_i))
                                if (rows[-1], cols[-1]) in row_col_pair:
                                    print "Part Two!"
                                    return
                                row_col_pair.append((rows[-1], cols[-1]))
                                vals.append(-1)
                    my_rhs.append(0)
                    my_sense += "L"
        row_offset += self.V * self.V * self.K
        """

        # constr. 9
        # The stream can be only relayed with the same quality, or be transcoded from higher quality to lower quality
        for src_i in xrange(self.V):
            for dst_i in xrange(self.V):
                for content_id in xrange(self.K):
                    
                    for path_id in xrange(self.N):
                        for quality_id in xrange(self.Q):
                            rows.append(row_offset + src_i*self.V*self.K + dst_i*self.K + content_id)
                            cols.append(self.get_gamma_column(src_i, dst_i, content_id, path_id, quality_id))
                            vals.append(self.get_quality(quality_id))
                    if self.get_lambda(src_i, content_id) == 1:
                        my_rhs.append(self.qualities[0])
                    else:
                        # constr. 3
                        for quality_i in xrange(self.Q):
                            for prev_src_i in xrange(self.N):
                                if prev_src_i == src_i: continue
                                for path_i in xrange(self.N):
                                    rows.append(row_offset + src_i*self.V*self.K + dst_i*self.K + content_id)
                                    cols.append(self.get_gamma_column(prev_src_i, src_i, content_id, path_i, quality_i))
                                    vals.append(-self.get_quality(quality_i))
                        my_rhs.append(0)
                    my_sense += "L"
        row_offset += self.V*self.V*self.K

        # constr. 10
        # Live contents can be streamed to a node with VMS placed
        for vms_i in xrange(self.V):
            for content_i in xrange(self.K):
                
                for path_i in xrange(self.N):
                    for src_i in xrange(self.V):
                        for quality_i in xrange(self.Q):
                            rows.append(row_offset + vms_i*self.K + content_i)
                            cols.append(self.get_gamma_column(src_i, vms_i, content_i, path_i, quality_i))
                            vals.append(1)
                rows.append(row_offset + vms_i*self.K + content_i)
                cols.append(vms_i)
                vals.append(-1)
                my_rhs.append(0)
                my_sense += "L"
        row_offset += self.V*self.K

        # constr. 14
        # Link bandwidth constraint
        for link_i in xrange(self.E):

            # constr. 12
            # Link utilization contributed by the delivery tree.
            for quality_i in xrange(self.Q):
                for path_i in xrange(self.N):
                    for content_i in xrange(self.K):
                        for src_i in xrange(self.V):
                            for dst_i in xrange(self.V):
                                rows.append(row_offset + link_i)
                                cols.append(self.get_gamma_column(src_i, dst_i, content_i, path_i, quality_i))
                                vals.append(self.get_quality(quality_i)*self.get_beta(src_i, dst_i, link_i, path_i))

            # constr. 13
            # Link utilization contributed by the user access traffic
            for src_i in xrange(self.V):
                for query_i in xrange(self.M):
                    for quality_i in xrange(self.Q):
                        rows.append(row_offset + link_i)
                        cols.append(self.get_alpha_column(src_i, query_i, quality_i))
                        val = 0
                        for dst_i in xrange(self.V):
                            beta = self.get_beta(src_i, dst_i, link_i, 0)
                            for content_i in xrange(self.K):
                                val += self.get_delta(dst_i, query_i, content_i) * beta
                        vals.append(self.get_quality(quality_i)*val)
            my_rhs.append(self.get_bandwidth(link_i))
            my_sense += "L"
        row_offset += self.E

        # constr. 15
        # Ingress bandwidth contributed by the deliver tree
        for vms_i in xrange(self.V):
            
            for content_i in xrange(self.K):
                # constr. 3
                for quality_i in xrange(self.Q):
                    for src_i in xrange(self.V):
                        for path_i in xrange(self.N):
                            rows.append(row_offset + vms_i)
                            cols.append(self.get_gamma_column(src_i, vms_i, content_i, path_i, quality_i))
                            vals.append(self.get_quality(quality_i))
            my_rhs.append(self.get_IO(vms_i))
            my_sense += "L"
        row_offset += self.V

        # constr. 16
        # Egress bandwidth utilization contributed by bothe the delivery tree and the user access
        for vms_i in xrange(self.V):

            for query_i in xrange(self.M):
                for quality_i in xrange(self.Q):
                    val = 0
                    for access_i in xrange(self.V):
                        for content_i in xrange(self.K):
                            val += self.get_delta(access_i, query_i, content_i)
                    rows.append(row_offset + vms_i)
                    cols.append(self.get_alpha_column(vms_i, query_i, quality_i))
                    vals.append(self.get_quality(quality_i) * val)

            for dst_i in xrange(self.V):
                for content_i in xrange(self.K):
                    for path_i in xrange(self.N):
                        for quality_i in xrange(self.Q):
                            rows.append(row_offset + vms_i)
                            cols.append(self.get_gamma_column(vms_i, dst_i, content_i, path_i, quality_i))
                            vals.append(self.get_quality(quality_i))
            my_rhs.append(self.get_IO(vms_i))
            my_sense += "L"
        row_offset += self.V

        # constr. 19
        for vms_i in xrange(self.V):

            for dst_i in xrange(self.V):
                for content_i in xrange(self.K):
                    for path_i in xrange(self.N):
                        for quality_i in xrange(self.Q):
                            rows.append(row_offset + vms_i)
                            cols.append(self.get_gamma_column(vms_i, dst_i, content_i, path_i, quality_i))
                            vals.append(0.3*self.ct+0.7*self.cf)

            for query_i in xrange(self.M):
                for quality_i in xrange(self.Q):
                    val = 0
                    for access_i in xrange(self.V):
                        for content_i in xrange(self.K):
                            val += self.get_delta(access_i, query_i, content_i)
                    rows.append(row_offset + vms_i)
                    cols.append(self.get_alpha_column(vms_i, query_i, quality_i))
                    vals.append((0.3*self.ct + 0.7*self.cf) * val)
            my_rhs.append(self.get_cloud(vms_i))
            my_sense += "L"
        row_offset += self.V

        # optimization goals
        my_obj = [0] * (self.V + self.V*self.V*self.K*self.N*self.Q + self.V*self.M*self.Q)
        # U part
        for link_i in xrange(self.E):
            for src_i in xrange(self.V):
                for dst_i in xrange(self.V):
                    for content_i in xrange(self.K):
                        for path_i in xrange(self.N):
                            for quality_i in xrange(self.Q):
                                my_obj[self.get_gamma_column(src_i, dst_i, content_i, path_i, quality_i)] += \
                                        self.get_quality(quality_i) * self.get_beta(src_i, dst_i, link_i, path_i) * self.wb
            for src_i in xrange(self.V):
                for query_i in xrange(self.M):
                    for quality_i in xrange(self.Q):
                        val = 0
                        for dst_i in xrange(self.V):
                            for content_i in xrange(self.K):
                                val += self.get_delta(dst_i, query_i, content_i)
                        my_obj[self.get_alpha_column(src_i, query_i, quality_i)] += self.get_quality(quality_i) * \
                                                                                    self.get_beta(src_i, dst_i, link_i, 0) * \
                                                                                    val * self.wb
        # C part
        for vms_i in xrange(self.V):
            for dst_i in xrange(self.V):
                for content_i in xrange(self.K):
                    for path_i in xrange(self.N):
                        for quality_i in xrange(self.Q):
                            my_obj[self.get_gamma_column(vms_i, dst_i, content_i, path_i, quality_i)] += self.wc * \
                                                                                                         (self.ct*0.3 + self.cf*0.7)
            for query_i in xrange(self.M):
                for quality_i in xrange(self.Q):
                    val = 0
                    for access_i in xrange(self.V):
                        for content_i in xrange(self.K):
                            val += self.get_delta(access_i, query_i, content_i)
                    my_obj[self.get_alpha_column(vms_i, query_i, quality_i)] += self.wc * \
                                                                                (self.ct*0.3 + self.cf*0.7) * \
                                                                                val

        # Upper bound of values
        my_ub = []
        for vms_i in xrange(self.V):
            if self.network.node[vms_i]['clouds'] != 0 or self.network.node[vms_i]['video_src'] != []:
                my_ub.append(1)
            else:
                my_ub.append(0)
        for src_i in xrange(self.V):
            for dst_i in xrange(self.V):
                for content_i in xrange(self.K):
                    for path_i in xrange(self.N):
                        for quality_i in xrange(self.Q):
                            if src_i == dst_i: my_ub.append(0)
                            else: my_ub.append(1)
        for vms_i in xrange(self.V):
            for content_i in xrange(self.M):
                for quality_i in xrange(self.Q):
                    if self.network.node[vms_i]['clouds'] != 0 or self.network.node[vms_i]['video_src'] != []:
                        my_ub.append(1)
                    else: my_ub.append(0)

        # Row names
        my_rownames = []
        for i in xrange(len(my_rhs)):
            my_rownames.append("c"+str(i+1))

        # Column names
        my_colnames = []
        for vms_i in xrange(self.V):
            my_colnames.append("rho"+str(i))
        for src_i in xrange(self.V):
            for dst_i in xrange(self.V):
                for content_i in xrange(self.K):
                    for path_i in xrange(self.N):
                        for quality_i in xrange(self.Q):
                            my_colnames.append("gamma("+
                                               str(src_i)+","+
                                               str(dst_i)+","+
                                               str(content_i)+","+
                                               str(path_i)+","+
                                               str(quality_i)+")")
        for vms_i in xrange(self.V):
            for query_i in xrange(self.M):
                for quality_i in xrange(self.Q):
                    my_colnames.append("alpha("+
                                       str(vms_i)+","+
                                       str(query_i)+","+
                                       str(quality_i)+")")

        prob.objective.set_sense(prob.objective.sense.minimize)
        prob.linear_constraints.add(rhs = my_rhs,
                                   senses = my_sense,
                                   names = my_rownames)
        prob.variables.add(obj = my_obj,
                           ub = my_ub,
                           names = my_colnames)
        prob.linear_constraints.set_coefficients(zip(rows, cols, vals))
